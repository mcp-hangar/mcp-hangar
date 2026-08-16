"""ADR-014's kill switch, asserted on the surface the gateway actually serves.

The governed task relay is gated by one flag, and both of its states matter: off
must be byte-identical to the relay-only stance -- no `tasks/*` handlers, no
advertised extension, nothing on the context -- because that is the retained
rollback path, and 2.0.0rc2 was cut to undo a release that advertised a wire it
did not serve (ADR-015).

The flag was on, then off, then on again, and the reason matters more than the
dates: it went back on once the SEP-2663 wire was actually served and verified
end to end, which is the condition ADR-015 Decision 5 set. So the thing to check
before flipping it again is not the flag, it is whether the served wire matches
what is advertised.

This suite used to build its server through `MCPServerFactory`, which no shipped
code called; it now calls the same two wiring functions `server/bootstrap` calls
(#956).
"""

from mcp_hangar import __version__, tasks_wire
from mcp_hangar._sdk_compat import HAS_NATIVE_TASKS, lowlevel_server, new_mcp_server
from mcp_hangar.protocol import HANGAR_SERVER_NAME
from mcp_hangar.fastmcp_server.task_relay_wiring import (
    advertise_tasks_capability,
    enable_governed_task_relay,
)
from mcp_hangar.server.context import get_context, reset_context

import pytest

# The SEP-2663 method set, sourced from the wire module so this file cannot claim
# a surface the server does not serve. `tasks/result` and `tasks/list` are absent:
# the SEP removes both (ADR-015).
_TASK_METHODS = tuple(sorted(tasks_wire.TASKS_METHODS))


@pytest.fixture
def _reset_ctx():
    """Isolate the singleton ApplicationContext around task-relay wiring tests."""
    reset_context()
    yield
    reset_context()


@pytest.mark.skipif(not HAS_NATIVE_TASKS, reason="v2-native Tasks SDK required")
class TestGovernedTaskRelayKillSwitch:
    """ADR-014: the task-relay serving surface is gated by the flag (both states asserted).

    Activated 2026-07-22 (flag defaults True). With the flag False the server is
    byte-identical to the relay-only stance (no tasks/* handlers, capabilities.tasks
    None) — the retained rollback path; with it True the governed relay is live.
    """

    def _served_low(self, *, enabled: bool):
        """Built the way `server/bootstrap` builds it, not the way the factory did.

        These two calls, in this order, are what `bootstrap/__init__.py` makes
        after `new_mcp_server(...)`. The suite used to reach the same handlers
        through `MCPServerFactory(config=ServerConfig(relay_tasks_enabled=...))`,
        a construction path no shipped code took -- so a kill switch could have
        worked here and not in production. It is now one seam, driven directly.
        """
        mcp = new_mcp_server(HANGAR_SERVER_NAME, version=__version__)
        enable_governed_task_relay(mcp, relay_tasks_enabled=enabled)
        advertise_tasks_capability(mcp, relay_tasks_enabled=enabled)
        return lowlevel_server(mcp)

    # -- dark parity (default OFF) ------------------------------------------

    def test_default_off_registers_no_tasks_handlers(self, _reset_ctx):
        low = self._served_low(enabled=False)
        for method in _TASK_METHODS:
            assert low.get_request_handler(method) is None, method

    def test_default_off_capabilities_tasks_is_none(self, _reset_ctx):
        low = self._served_low(enabled=False)
        assert low.get_capabilities().tasks is None

    def test_default_off_context_task_wiring_absent(self, _reset_ctx):
        self._served_low(enabled=False)
        ctx = get_context()
        assert ctx.governed_task_store is None
        assert ctx.task_consent_gate is None
        assert ctx.task_upstream_router is None

    def test_default_off_capabilities_byte_identical_except_the_tasks_extension(self, _reset_ctx):
        """The ONLY advertised-capabilities difference is the tasks extension entry."""
        from mcp_hangar.tasks_wire import EXTENSION_ID

        off = self._served_low(enabled=False).get_capabilities().model_dump()
        reset_context()
        on = self._served_low(enabled=True).get_capabilities().model_dump()

        assert EXTENSION_ID not in (off.get("extensions") or {})
        on_without_tasks = dict(on)
        on_without_tasks["extensions"] = {
            key: value for key, value in (on.get("extensions") or {}).items() if key != EXTENSION_ID
        } or None
        assert off == on_without_tasks

    def test_no_tasks_update_handler_when_off(self, _reset_ctx):
        low = self._served_low(enabled=False)
        assert low.get_request_handler("tasks/update") is None

    # -- enabled (flag True) -------------------------------------------------

    def test_enabled_registers_the_sep_2663_tasks_handlers(self, _reset_ctx):
        low = self._served_low(enabled=True)
        for method in _TASK_METHODS:
            assert low.get_request_handler(method) is not None, method

    def test_enabled_does_not_register_the_methods_sep_2663_removed(self, _reset_ctx):
        """Not registering them is how they return -32601; nothing else implements that."""
        low = self._served_low(enabled=True)
        for method in ("tasks/result", "tasks/list"):
            assert low.get_request_handler(method) is None, method

    def test_enabled_advertises_the_tasks_extension(self, _reset_ctx):
        """Advertised under `extensions`, which is where SEP-2663 puts it.

        Not under `capabilities.tasks`: that field does not exist in
        the 2026-07-28 `ServerCapabilities`, so the SDK's per-version serialization
        sieve drops it from a modern `server/discover` -- leaving the surface
        served but undiscoverable.
        """
        from mcp_hangar.tasks_wire import EXTENSION_ID

        low = self._served_low(enabled=True)
        extensions = low.get_capabilities().extensions or {}

        assert EXTENSION_ID in extensions

    def test_the_advertised_extension_names_only_served_methods(self, _reset_ctx):
        """The ad and the registration read the same set, so they cannot drift.

        `tasks/list` is absent from it, so it cannot be advertised by mistake --
        which is the defect 2.0.0rc2 was cut to undo.
        """
        from mcp_hangar.tasks_wire import EXTENSION_ID, TASKS_METHODS

        low = self._served_low(enabled=True)
        settings = (low.get_capabilities().extensions or {})[EXTENSION_ID]

        assert set(settings["methods"]) == set(TASKS_METHODS)
        assert "tasks/list" not in settings["methods"]
        for method in settings["methods"]:
            assert low.get_request_handler(method) is not None, method

    def test_the_legacy_tasks_capability_field_is_not_used(self, _reset_ctx):
        """It survives only on the wire where Hangar refuses to serve tasks."""
        low = self._served_low(enabled=True)

        assert low.get_capabilities().tasks is None

    def test_enabled_exposes_store_gate_router_on_context(self, _reset_ctx):
        from mcp_hangar.application.tasks import GovernedTaskStore
        from mcp_hangar.domain.services.task_consent import TaskConsentGate

        self._served_low(enabled=True)
        ctx = get_context()
        assert isinstance(ctx.governed_task_store, GovernedTaskStore)
        assert isinstance(ctx.task_consent_gate, TaskConsentGate)
        assert callable(ctx.task_upstream_router)

    def test_enabled_context_store_is_same_instance_handlers_hold(self, _reset_ctx, monkeypatch):
        """The store/router exposed on ctx are the SAME instances passed to the handlers."""
        import mcp_hangar.fastmcp_server.task_relay_handlers as trh

        captured: dict = {}
        real = trh.register_task_relay_handlers

        def _spy(mcp, store, consent_gate, upstream_router):
            captured["store"] = store
            captured["consent_gate"] = consent_gate
            captured["router"] = upstream_router
            return real(mcp, store, consent_gate, upstream_router)

        monkeypatch.setattr(trh, "register_task_relay_handlers", _spy)
        self._served_low(enabled=True)
        ctx = get_context()
        assert captured["store"] is ctx.governed_task_store
        assert captured["consent_gate"] is ctx.task_consent_gate
        assert captured["router"] is ctx.task_upstream_router

    def test_tasks_update_is_registered_when_enabled(self, _reset_ctx):
        """It was previously gated on an SDK probe that could never become true."""
        low = self._served_low(enabled=True)
        assert low.get_request_handler("tasks/update") is not None


class TestTheShippedDefault:
    """What a deployment gets when nobody sets the flag.

    There used to be three defaults for it -- `ServerConfig`, the fluent
    builder's `with_config()`, and the literal on the serve path -- and they
    disagreed (True / False / True), so the posture depended on how the server
    was built. Two of the three are gone with the factory (#954, #956). This
    pins the one that is left, which is the one a real deployment reads.

    It is asserted against the source because the default is a literal inside a
    `dict.get()` on the serve path, not an importable symbol -- the same guard
    style as `test_no_mcp_logging_dependency`.
    """

    def test_the_serve_path_defaults_the_relay_on(self):
        import inspect
        from pathlib import Path

        import mcp_hangar

        bootstrap_src = (Path(inspect.getfile(mcp_hangar)).parent / "server" / "bootstrap" / "__init__.py").read_text()

        assert 'full_config.get("relay_tasks_enabled", True)' in bootstrap_src, (
            "the HTTP-serve bootstrap no longer defaults the governed relay on, "
            "or the flag moved -- ADR-015 Decision 5 gates that change"
        )
