"""Hangar forwards the caller's Tasks declaration upstream, per request.

Two halves, and the first is a bug the second could not have worked around.

**Reading.** `read_protocol_negotiation` looked for
`io.modelcontextprotocol/capabilities`. The spec key is
`io.modelcontextprotocol/clientCapabilities` -- the SDK's inbound ladder requires
it on every modern request, and the short spelling appears nowhere in
`mcp_types`. So capabilities came back empty for every well-formed request. It
went unnoticed because nothing consumed them.

**Forwarding.** SEP-2663 leaves task augmentation to the *upstream* and gates it
on the **caller** having declared the extension. On the wire to an upstream,
Hangar is that caller. Declaring nothing means a spec-following upstream never
mints a task, and the whole governed relay sits idle having never been offered
one.

The forwarding is conditional on purpose, and each condition excludes a specific
way of lying to an upstream.

**One reading (#1492).** Whether the caller declared the extension is read once
per request, by the task relay's server middleware, and bound as
`caller_polls_tasks_var`. Forwarding reads that bound fact rather than re-reading
the declaration out of the negotiation a frame later, so what Hangar asks an
upstream for and what the relay seam may hand back cannot disagree.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from mcp_hangar.context import caller_polls_tasks_var
from mcp_hangar.negotiation import read_protocol_negotiation
from mcp_hangar.protocol import (
    TASKS_EXTENSION_ID,
    forwardable_client_capabilities,
    inject_protocol_meta,
)

_SPEC_KEY = "io.modelcontextprotocol/clientCapabilities"
_LEGACY_KEY = "io.modelcontextprotocol/capabilities"
_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"

_DECLARED: dict[str, Any] = {"extensions": {TASKS_EXTENSION_ID: {}}}


@pytest.fixture
def declaring_caller() -> Iterator[None]:
    """Bind a request whose caller declared the Tasks extension.

    What the task relay's middleware binds for such a caller: it speaks a
    revision with `tasks/*` and it declared the extension, so it can poll a task
    it is handed.
    """
    token = caller_polls_tasks_var.set(True)
    yield
    caller_polls_tasks_var.reset(token)


@pytest.fixture
def relay_wired(monkeypatch: pytest.MonkeyPatch):
    """Pretend the governed relay is wired, without booting a server.

    Sets the protocol layer's own flag. This used to patch `server.context` --
    the protocol module read `ctx.governed_task_store` directly, which is the
    three-layer reach the flag replaced.
    """
    import mcp_hangar.protocol as protocol_module

    monkeypatch.setattr(protocol_module, "_task_relay_wired", True)
    yield


class TestReadingTheCallersDeclaration:
    def test_the_spec_key_is_read(self):
        """`clientCapabilities`, not `capabilities`.

        The SDK's inbound ladder requires this exact key on every modern
        request, so reading the other spelling meant reading nothing, always.
        """
        negotiation = read_protocol_negotiation({_VERSION_KEY: "2026-07-28", _SPEC_KEY: _DECLARED})

        assert dict(negotiation.capabilities) == _DECLARED

    def test_the_legacy_spelling_still_parses(self):
        """Accepted so a caller that copied the old key keeps working."""
        negotiation = read_protocol_negotiation({_LEGACY_KEY: _DECLARED})

        assert dict(negotiation.capabilities) == _DECLARED

    def test_the_spec_key_wins_over_the_legacy_one(self):
        negotiation = read_protocol_negotiation({_SPEC_KEY: _DECLARED, _LEGACY_KEY: {"extensions": {"other": {}}}})

        assert dict(negotiation.capabilities) == _DECLARED

    def test_garbage_is_still_fail_safe(self):
        """The reader must never raise on a hostile envelope."""
        assert dict(read_protocol_negotiation({_SPEC_KEY: "not-a-mapping"}).capabilities) == {}
        assert dict(read_protocol_negotiation(None).capabilities) == {}


class TestForwardingTheDeclaration:
    def test_a_declaring_caller_with_the_relay_wired_is_forwarded(self, declaring_caller, relay_wired):
        assert forwardable_client_capabilities() == {"extensions": {TASKS_EXTENSION_ID: {}}}

    def test_it_lands_on_the_outbound_meta(self, declaring_caller, relay_wired):
        """This is what actually reaches the upstream's inbound ladder."""
        params = inject_protocol_meta({"name": "some_tool", "arguments": {}})

        assert params["_meta"][_SPEC_KEY] == {"extensions": {TASKS_EXTENSION_ID: {}}}
        # Non-mutating, per the function's contract.
        assert "arguments" in params

    def test_a_caller_that_declared_nothing_is_not_spoken_for(self, relay_wired):
        """A connection-level claim would mint tasks for clients that never asked.

        And Hangar would then answer that same client `-32021` on `tasks/get`,
        leaving it holding a handle it cannot use. The two ends have to agree.
        """
        assert forwardable_client_capabilities() is None
        assert _SPEC_KEY not in inject_protocol_meta({})["_meta"]

    def test_a_path_the_middleware_never_wrapped_declares_nothing(self, relay_wired):
        """The unbound default, and the same direction the relay seam takes (#1492).

        A request nothing bound is one whose caller cannot poll a task, so the
        seam would hand it none -- soliciting one from an upstream would create
        work only to refuse and cancel it.
        """
        assert caller_polls_tasks_var.get() is False
        assert forwardable_client_capabilities() is None

    def test_nothing_is_claimed_while_the_relay_is_off(self, declaring_caller, monkeypatch):
        """Claiming it with no governed store promises governance that is not running."""
        import mcp_hangar.protocol as protocol_module

        monkeypatch.setattr(protocol_module, "_task_relay_wired", False)

        assert forwardable_client_capabilities() is None

    def test_only_the_tasks_extension_is_relayed(self, declaring_caller, relay_wired):
        """Not a passthrough: Hangar claims only what it can itself service.

        Forwarding an arbitrary declaration would have Hangar vouch for
        extensions it does not implement on the caller's behalf. What is sent is
        built here, from the one extension Hangar serves, and never copied from
        what the caller sent -- so a caller declaring a dozen others still has
        exactly this one relayed.
        """
        assert forwardable_client_capabilities() == {"extensions": {TASKS_EXTENSION_ID: {}}}

    def test_a_caller_set_key_is_not_clobbered(self, declaring_caller, relay_wired):
        """`inject_protocol_meta` is set-if-absent for every key it manages."""
        params = inject_protocol_meta({"_meta": {_SPEC_KEY: {"extensions": {}}}})

        assert params["_meta"][_SPEC_KEY] == {"extensions": {}}

    def test_a_broken_capability_read_degrades_to_declaring_nothing(self, relay_wired, monkeypatch):
        """Fault barrier: a capability read must never fail an invoke.

        Degrading to "declare nothing" is the safe direction -- it loses task
        augmentation, it does not break the call.

        Aimed at the read of the bound fact, which is what can still raise. It
        used to point at the negotiation read, and before that at `get_context`,
        back when this function reached into the server layer to ask whether the
        relay was wired.

        Patched on `context`, not on `protocol`: that import is inside the
        function, so the name is resolved fresh from the source module on every
        call.
        """
        import mcp_hangar.context as context_module

        class _Boom:
            def get(self) -> bool:
                raise RuntimeError("no bound capability")

        monkeypatch.setattr(context_module, "caller_polls_tasks_var", _Boom())

        assert forwardable_client_capabilities() is None
        assert inject_protocol_meta({})["_meta"][_VERSION_KEY]  # the rest still works
