"""A reload applies the whole configuration, and keeps the topology mode (#1424).

A reload used to reset the topology mode to `egress` and apply only
`mcp_servers`: `interceptors`, `ui_resources`, `headers.param_validation`,
`resource_links` and `execution` kept their boot values, a block deleted from
the file stayed in force, policies set at runtime were wiped, and between
clearing the policies and registering them again a call was resolved against
none. Each of those is pinned here through the real `ReloadConfigurationHandler`
and `ServerConfigLoader`, on the process's own resolver, registry and runtime.

The served-path versions -- a real app over streamable HTTP, reloaded over REST,
SIGHUP and the file watcher -- are in `tests/integration/test_a_reload_*`, and
the black-box one against a running `mcp-hangar` is in `tests/live`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any
from collections.abc import Callable
from unittest.mock import Mock

import pytest
import yaml

from mcp_hangar.application.commands import ReloadConfigurationCommand
from mcp_hangar.application.commands.crud_commands import SetL7PolicyCommand
from mcp_hangar.application.commands.crud_handlers import SetL7PolicyHandler
from mcp_hangar.application.commands.reload_handler import ReloadConfigurationHandler
from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.events import ConfigurationReloadFailed
from mcp_hangar.domain.exceptions import (
    ConfigurationError,
    ConfigurationRestartRequiredError,
    ConfigurationUnavailableError,
)
from mcp_hangar.domain.policies.egress_l7 import L7Policy, ToolRules
from mcp_hangar.domain.policies.header_exposure import clear_header_exposure_policies, get_header_exposure_policy
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.services.ui_resource_guard import get_ui_resource_guard, reset_ui_resource_guard
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.fastmcp_server import flat_tool_projection
from mcp_hangar.fastmcp_server import resource_link_read_through as rt
from mcp_hangar.server import config as server_config
from mcp_hangar.server.api import middleware
from mcp_hangar.server.config import load_configuration, ServerConfigLoader
from mcp_hangar.server.context import get_context
from mcp_hangar.server.state import get_runtime, GROUPS
from mcp_hangar.server.tools import batch
from mcp_hangar.server.tools.batch.concurrency import (
    DEFAULT_GLOBAL_CONCURRENCY,
    DEFAULT_PROVIDER_CONCURRENCY,
    get_concurrency_manager,
    reset_concurrency_manager,
)

SERVER = "store"
TENANT = "tenant:a"
PIN = "a" * 64
DENY_Y = ToolAccessPolicy(deny_list=("y",))
PAYLOAD_CAP = {"type": "payload_size", "max_bytes": 64}
#: Every server id a test here adds, so teardown removes exactly those.
IDS = (SERVER, "extra", "late", "other", "m1", "bad", "docker", "remote")


def _server(**extra: Any) -> dict[str, Any]:
    """A server that is never started: reload only builds, stops and replaces it."""
    return {"mode": "subprocess", "command": ["python", "-c", "pass"], **extra}


def _config(*, mode: str | None = None, servers: dict[str, Any] | None = None, **sections: Any) -> dict[str, Any]:
    config: dict[str, Any] = {"mcp_servers": servers if servers is not None else {SERVER: _server()}}
    if mode is not None:
        config["tool_access"] = {"mode": mode}
    config.update(sections)
    return config


def _reset() -> None:
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    clear_header_exposure_policies()
    batch.configure_interceptors(None)
    flat_tool_projection.set_param_validation_required(False)
    rt.set_max_links_per_tenant(rt.DEFAULT_MAX_LINKS_PER_TENANT)
    reset_ui_resource_guard()
    reset_concurrency_manager()
    server_config._BUILT_FROM.clear()
    repository = get_runtime().repository
    for mcp_server_id in IDS:
        if repository.exists(mcp_server_id):
            repository.remove(mcp_server_id)
    GROUPS.clear()


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield
    _reset()


class _Gateway:
    """A config file, the process state it was booted into, and the reload handler bootstrap wires."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.events = Mock()
        self.handler = ReloadConfigurationHandler(
            get_runtime().repository, self.events, str(path), config_loader=ServerConfigLoader(), groups=GROUPS
        )

    def write(self, config: dict[str, Any]) -> None:
        self.path.write_text(yaml.safe_dump(config, sort_keys=False))

    def boot(self, config: dict[str, Any]) -> None:
        """What bootstrap does with a file: every process-wide section, then the servers."""
        self.write(config)
        load_configuration(str(self.path))

    def reload(self, config: dict[str, Any]) -> dict[str, Any]:
        self.write(config)
        return self.handler.handle(ReloadConfigurationCommand(requested_by="test"))


@pytest.fixture
def gateway(tmp_path: Path) -> _Gateway:
    return _Gateway(tmp_path / "config.yaml")


def _spy_on_shutdown(monkeypatch: pytest.MonkeyPatch, mcp_server_id: str) -> tuple[Any, Mock]:
    server = get_runtime().repository.get(mcp_server_id)
    shutdown = Mock()
    monkeypatch.setattr(server, "shutdown", shutdown)
    return server, shutdown


class TestTheTopologyModeSurvivesAReload:
    def test_an_unchanged_front_door_is_still_a_front_door(self, gateway: _Gateway) -> None:
        config = _config(mode="front_door")
        gateway.boot(config)

        gateway.reload(config)

        resolver = get_tool_access_resolver()
        assert resolver.topology_mode == "front_door"
        assert not resolver.is_tool_allowed(SERVER, "anything"), "a caller with no tenant is still denied"
        assert resolver.is_tool_allowed(SERVER, "anything", member_id=TENANT)


class TestAModeChangeIsRefused:
    @pytest.mark.parametrize(("running", "requested"), [("front_door", "egress"), ("egress", "front_door")])
    def test_it_is_refused_and_changes_nothing(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch, running: str, requested: str
    ) -> None:
        gateway.boot(_config(mode=running, servers={SERVER: _server(tools={"deny_list": ["x"]})}))
        before, shutdown = _spy_on_shutdown(monkeypatch, SERVER)

        with pytest.raises(ConfigurationRestartRequiredError, match="Restart the gateway") as refused:
            gateway.reload(_config(mode=requested, servers={"other": _server()}))

        assert isinstance(refused.value, ConfigurationError)
        assert refused.value.details == {"running_mode": running, "requested_mode": requested}
        resolver = get_tool_access_resolver()
        assert resolver.topology_mode == running
        assert resolver.get_configured_policy("provider", SERVER) == ToolAccessPolicy(deny_list=("x",))
        repository = get_runtime().repository
        assert repository.get(SERVER) is before
        assert repository.get("other") is None
        shutdown.assert_not_called()
        (failed,) = [
            c.args[0] for c in gateway.events.publish.call_args_list if isinstance(c.args[0], ConfigurationReloadFailed)
        ]
        assert failed.error_type == "ConfigurationRestartRequiredError"

    def test_the_api_answers_409_not_500(self) -> None:
        refused = ConfigurationRestartRequiredError("restart")

        status = next(code for kind, code in middleware._EXCEPTION_STATUS_MAP if isinstance(refused, kind))

        assert status == 409


class TestABadSectionChangesNothing:
    @pytest.mark.parametrize(
        ("section", "named"),
        [
            ({"resource_links": {"max_per_tenant": 0}}, "resource_links.max_per_tenant"),
            ({"headers": {"param_validation": {"required": "yes"}}}, "headers.param_validation.required"),
            ({"interceptors": {"validators": [{"type": "no_such_validator"}]}}, "interceptors.validators"),
            ({"execution": {"max_concurrency": "many"}}, "concurrency limit"),
            ({"tool_access": {"mode": "front-door"}}, "tool_access.mode"),
        ],
        ids=["resource_links", "param_validation", "interceptors", "execution", "tool_access"],
    )
    def test_the_reload_is_refused_before_any_server_stops(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch, section: dict[str, Any], named: str
    ) -> None:
        gateway.boot(_config(interceptors={"validators": [PAYLOAD_CAP]}))
        before, shutdown = _spy_on_shutdown(monkeypatch, SERVER)

        with pytest.raises(ConfigurationError, match=named.replace(".", r"\.")):
            gateway.reload(_config(**section))

        shutdown.assert_not_called()
        assert get_runtime().repository.get(SERVER) is before
        assert len(batch._executor._validator_pipeline._validators) == 1, "the running validators stay"


def _validators() -> int:
    return len(batch._executor._validator_pipeline._validators)


def _allowed_ui() -> list[str]:
    guard = get_ui_resource_guard()
    return [uri for uri in ("ui://reports/q3", "ui://dash/q3") if guard.evaluate(uri, TENANT).allowed]


def _limits() -> tuple[int, int]:
    manager = get_concurrency_manager()
    return manager.global_limit, manager.default_mcp_server_limit


#: section -> (booted block, edited block, reader, booted value, edited value, value once deleted)
SECTIONS: dict[str, tuple[Any, Any, Callable[[], Any], Any, Any, Any]] = {
    "interceptors": ({"validators": [PAYLOAD_CAP]}, {"validators": [PAYLOAD_CAP, PAYLOAD_CAP]}, _validators, 1, 2, 0),
    "ui_resources": (
        {"tenants": {TENANT: {"allowlist": ["ui://reports/"]}}},
        {"tenants": {TENANT: {"allowlist": ["ui://dash/"]}}},
        _allowed_ui,
        ["ui://reports/q3"],
        ["ui://dash/q3"],
        [],
    ),
    "headers": (
        {"param_validation": {"required": False}},
        {"param_validation": {"required": True}},
        flat_tool_projection.param_validation_required,
        False,
        True,
        False,
    ),
    "resource_links": (
        {"max_per_tenant": 17},
        {"max_per_tenant": 5},
        lambda: rt._MAX_LINKS_PER_TENANT,
        17,
        5,
        rt.DEFAULT_MAX_LINKS_PER_TENANT,
    ),
    "execution": (
        {"max_concurrency": 7, "default_mcp_server_concurrency": 3},
        {"max_concurrency": 2, "default_mcp_server_concurrency": 1},
        _limits,
        (7, 3),
        (2, 1),
        (DEFAULT_GLOBAL_CONCURRENCY, DEFAULT_PROVIDER_CONCURRENCY),
    ),
}


class TestEveryProcessSectionIsApplied:
    @pytest.mark.parametrize("section", sorted(SECTIONS))
    def test_an_edit_takes_effect_and_a_deleted_block_is_removed(self, gateway: _Gateway, section: str) -> None:
        booted, edited, read, booted_value, edited_value, default = SECTIONS[section]
        gateway.boot(_config(**{section: booted}))
        assert read() == booted_value

        gateway.reload(_config(**{section: edited}))
        assert read() == edited_value

        gateway.reload(_config())
        assert read() == default

    def test_the_ui_consent_gate_survives_a_reload(self, gateway: _Gateway) -> None:
        """Bootstrap attaches the gate after config load; a reload must not build a guard without it."""

        class _Consent:
            def __init__(self) -> None:
                self.asked: list[str] = []

            async def request_consent(
                self, uri: str, tenant_id: str | None, mcp_server_id: str, correlation_id: str
            ) -> bool:
                self.asked.append(uri)
                return True

        gateway.boot(_config(ui_resources={"tenants": {TENANT: {"allowlist": ["ui://reports/"]}}}))
        consent = _Consent()
        get_ui_resource_guard().attach_consent_gate(consent)

        gateway.reload(_config(ui_resources={"tenants": {TENANT: {"allowlist": ["ui://dash/"]}}}))

        decision = asyncio.run(get_ui_resource_guard().enforce("ui://dash/q3", TENANT, SERVER))
        assert decision.allowed
        assert consent.asked == ["ui://dash/q3"]


GOVERNED = _server(
    tools={"deny_list": ["x"]},
    tool_projection={"withdrawn": ["w"], "pins": {"p": PIN}},
    header_exposure={"deny_annotated": ["*secret*"]},
)


def _governance(mcp_server_id: str) -> dict[str, bool]:
    registry = get_tool_projection_registry()
    return {
        "denies_x": not get_tool_access_resolver().is_tool_allowed(mcp_server_id, "x"),
        "withdrawn": registry.is_withdrawn(mcp_server_id, "w"),
        "pinned": registry.resolve_pin(mcp_server_id, "p", None) is not None,
        "header_exposure": get_header_exposure_policy(mcp_server_id) is not None,
    }


#: A group whose OWN controls hide `y` (deny) and `gw` (withdrawal) from its
#: member m1, a top-level server. The front door finds them only through the
#: group registry.
GROUP = {
    "mode": "group",
    "auto_start": False,
    "tools": {"deny_list": ["y"]},
    "tool_projection": {"withdrawn": ["gw"]},
    "members": [{"id": "m1"}],
}


def _group_rules() -> dict[str, bool]:
    """What the front door decides for a tenant on the group's member m1."""
    return {
        "y_allowed": flat_tool_projection.is_governed_allowed("m1", "y", kind="tool", tenant_id=TENANT),
        "gw_allowed": flat_tool_projection.is_governed_allowed("m1", "gw", kind="tool", tenant_id=TENANT),
    }


def _process_state() -> dict[str, Any]:
    """Every process-wide section, as its reader sees it."""
    return {section: read() for section, (_booted, _edited, read, *_values) in SECTIONS.items()}


class TestGovernanceAcrossAReload:
    def test_what_the_file_no_longer_declares_is_lifted(self, gateway: _Gateway) -> None:
        gateway.boot(_config(servers={SERVER: GOVERNED}))
        assert set(_governance(SERVER).values()) == {True}

        gateway.reload(_config())

        assert set(_governance(SERVER).values()) == {False}

    def test_a_runtime_policy_is_kept_and_a_removed_servers_goes_with_it(self, gateway: _Gateway) -> None:
        gateway.boot(_config(servers={SERVER: _server(), "extra": _server()}))
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy("_global", DENY_Y)  # the agent's `_global` policy
        resolver.set_group_policy("g", DENY_Y)  # a REST group-scope policy
        resolver.set_mcp_server_policy("extra", DENY_Y)  # as `hangar_load` or REST would

        gateway.reload(_config(servers={SERVER: _server()}))

        assert resolver.get_configured_policy("provider", "_global") == DENY_Y
        assert resolver.get_configured_policy("group", "g") == DENY_Y
        assert resolver.get_configured_policy("provider", "extra") is None, "as on hangar_unload"

    def test_the_file_replaces_a_runtime_policy_on_the_same_scope(self, gateway: _Gateway) -> None:
        gateway.boot(_config())
        get_tool_access_resolver().set_mcp_server_policy(SERVER, DENY_Y)

        gateway.reload(_config(servers={SERVER: _server(tools={"deny_list": ["x"]})}))

        assert get_tool_access_resolver().get_configured_policy("provider", SERVER) == ToolAccessPolicy(
            deny_list=("x",)
        )

    def test_a_group_and_its_inline_member_come_back_with_their_policies(self, gateway: _Gateway) -> None:
        group = {
            "mode": "group",
            "auto_start": False,
            "tools": {"deny_list": ["y"]},
            "members": [{"id": "m1", **_server(tools={"deny_list": ["x"]})}],
        }
        gateway.boot(_config(servers={"g": group}))

        gateway.reload(_config(servers={"g": group}))

        member = GROUPS["g"].get_member("m1")
        assert member is not None and member.mcp_server is get_runtime().repository.get("m1")
        resolver = get_tool_access_resolver()
        assert resolver.get_configured_policy("group", "g") == ToolAccessPolicy(deny_list=("y",))
        assert resolver.get_configured_policy("member", "g:m1") == ToolAccessPolicy(deny_list=("x",))

    def test_a_runtime_policy_on_an_inline_member_survives_an_unchanged_reload(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An inline member is declared by the file: not removed, not stopped, not stripped."""
        member = {"id": "m1", **_server()}
        config = _config(servers={"g": {"mode": "group", "auto_start": False, "members": [member]}})
        gateway.boot(config)
        get_tool_access_resolver().set_mcp_server_policy("m1", DENY_Y)  # a REST provider-scope policy
        _, shutdown = _spy_on_shutdown(monkeypatch, "m1")

        result = gateway.reload(config)

        assert result["mcp_servers_removed"] == []
        assert result["mcp_servers_unchanged"] == ["m1"]
        shutdown.assert_not_called()
        assert get_tool_access_resolver().get_configured_policy("provider", "m1") == DENY_Y

    def test_a_policy_the_rest_endpoint_stored_is_replayed_over_the_file(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """As startup replays the store after the file, so a reload and a restart agree."""
        stored = [("provider", SERVER, DENY_Y), ("group", "g", DENY_Y)]
        store = SimpleNamespace(list_all_policies=lambda: stored)
        monkeypatch.setattr(get_context(), "auth_components", SimpleNamespace(tap_store=store))
        governed = _config(servers={SERVER: _server(tools={"deny_list": ["x"]})})
        gateway.boot(governed)
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy(SERVER, DENY_Y)  # what the REST endpoint does beside storing it

        gateway.reload(governed)

        assert resolver.get_configured_policy("provider", SERVER) == DENY_Y
        assert resolver.get_configured_policy("group", "g") == DENY_Y


MOCK_PROVIDER = Path(__file__).resolve().parents[1] / "mock_provider.py"


class TestAnUnchangedServerIsKept:
    def test_a_running_server_and_an_inline_member_stay_the_running_objects(self, gateway: _Gateway) -> None:
        """Not replaced by idle copies, which left the running processes with nothing to stop them (#1424)."""
        real = {"mode": "subprocess", "command": [sys.executable, str(MOCK_PROVIDER)]}
        group = {"mode": "group", "auto_start": False, "members": [{"id": "m1", **real}]}
        config = _config(servers={SERVER: real, "g": group})
        gateway.boot(config)
        repository = get_runtime().repository
        running = {mcp_server_id: repository.get(mcp_server_id) for mcp_server_id in (SERVER, "m1")}
        try:
            for server in running.values():
                server.ensure_ready()
            states = {mcp_server_id: server.state for mcp_server_id, server in running.items()}

            result = gateway.reload(config)

            assert result["mcp_servers_unchanged"] == ["m1", SERVER]
            assert all(repository.get(mcp_server_id) is server for mcp_server_id, server in running.items())
            member = GROUPS["g"].get_member("m1")
            assert member is not None and member.mcp_server is running["m1"]
            assert {mcp_server_id: server.state for mcp_server_id, server in running.items()} == states
        finally:
            for server in running.values():
                server.shutdown()

    def test_a_server_whose_entry_changed_is_stopped_before_it_is_replaced(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A server whose settings changed is stopped before it is replaced."""
        gateway.boot(_config(servers={SERVER: _server(description="before")}))
        before, shutdown = _spy_on_shutdown(monkeypatch, SERVER)

        result = gateway.reload(_config(servers={SERVER: _server(description="after")}))

        assert result["mcp_servers_updated"] == [SERVER]
        shutdown.assert_called_once()
        assert get_runtime().repository.get(SERVER) is not before

    @pytest.mark.parametrize(
        "edit",
        [
            {"env": {"TOKEN": "from-rest"}},
            {"description": "edited at runtime"},
            {"idle_ttl_s": 999},
            {"health_check_interval_s": 17},
        ],
        ids=["env", "description", "idle_ttl_s", "health_check_interval_s"],
    )
    def test_a_rest_edit_is_undone_by_a_reload_as_by_a_restart(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch, edit: dict[str, Any]
    ) -> None:
        """The file text is unchanged, but the running server no longer reads as the file builds it.

        Keeping it kept the REST edit while the reload reported the server as
        updated, and a restart applies the file.
        """
        spec = _server(env={"TOKEN": "from-file"}, description="from file")
        gateway.boot(_config(servers={SERVER: spec}))
        before, shutdown = _spy_on_shutdown(monkeypatch, SERVER)
        before.update_config(**edit)  # what the REST update endpoint does to the running server

        result = gateway.reload(_config(servers={SERVER: spec}))

        after = get_runtime().repository.get(SERVER)
        assert result["mcp_servers_updated"] == [SERVER]
        shutdown.assert_called_once()
        assert after is not before
        assert (after._env, after._description) == ({"TOKEN": "from-file"}, "from file")

    def test_the_replacement_starts_with_the_files_env(self, gateway: _Gateway) -> None:
        """The process a reload starts after a REST edit of `env` runs with the file's value.

        The mock upstream names its `add` tool after `MOCK_ADD_DESCRIPTION`, so
        the listed tool says which environment the process was started with.
        """
        spec = {
            "mode": "subprocess",
            "command": [sys.executable, str(MOCK_PROVIDER)],
            "env": {"MOCK_ADD_DESCRIPTION": "from the file"},
        }
        gateway.boot(_config(servers={SERVER: spec}))
        repository = get_runtime().repository
        before = repository.get(SERVER)
        after = None
        try:
            before.ensure_ready()
            before.update_config(env={"MOCK_ADD_DESCRIPTION": "from REST"})

            gateway.reload(_config(servers={SERVER: spec}))

            after = repository.get(SERVER)
            assert after is not before
            after.ensure_ready()
            assert after.get_tools_dict()["add"].description == "from the file"
        finally:
            for server in (before, after):
                if server is not None:
                    server.shutdown()


DENY_Z = ToolAccessPolicy(deny_list=("z",))


class _PolicyStore:
    """The REST endpoint's policy store, as the replay reads it."""

    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.failing_reads: tuple[int, ...] = ()
        self.delay = 0.0
        self.reads = 0
        self.reading = threading.Event()

    def list_all_policies(self) -> list[Any]:
        self.reads += 1
        self.reading.set()
        time.sleep(self.delay)
        if self.reads in self.failing_reads:
            raise OSError("database is locked")
        return list(self.rows)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _PolicyStore:
    policy_store = _PolicyStore([("provider", SERVER, DENY_Y)])
    monkeypatch.setattr(get_context(), "auth_components", SimpleNamespace(tap_store=policy_store))
    return policy_store


class TestTheStoredPoliciesAcrossAReload:
    GOVERNED_X = _server(tools={"deny_list": ["x"]})

    def _boot(self, gateway: _Gateway) -> None:
        gateway.boot(_config(servers={SERVER: self.GOVERNED_X}))
        get_tool_access_resolver().set_mcp_server_policy(SERVER, DENY_Y)  # what the REST endpoint does beside storing

    def test_a_store_that_cannot_be_read_refuses_the_reload(
        self, gateway: _Gateway, store: _PolicyStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Applying the file without the stored rows would loosen `y` and report success."""
        self._boot(gateway)
        before, shutdown = _spy_on_shutdown(monkeypatch, SERVER)
        store.failing_reads = (1,)

        with pytest.raises(ConfigurationUnavailableError, match="nothing was changed"):
            gateway.reload(_config(servers={SERVER: self.GOVERNED_X, "late": _server()}))

        shutdown.assert_not_called()
        repository = get_runtime().repository
        assert repository.get(SERVER) is before
        assert repository.get("late") is None
        assert get_tool_access_resolver().get_configured_policy("provider", SERVER) == DENY_Y

    def test_a_failed_read_before_the_swap_uses_the_rows_read_when_prepared(
        self, gateway: _Gateway, store: _PolicyStore
    ) -> None:
        self._boot(gateway)
        store.failing_reads = (2,)

        gateway.reload(_config(servers={SERVER: self.GOVERNED_X}))

        assert store.reads == 2
        assert get_tool_access_resolver().get_configured_policy("provider", SERVER) == DENY_Y

    def test_a_rest_write_made_while_the_reload_runs_is_not_undone(
        self, gateway: _Gateway, store: _PolicyStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._boot(gateway)
        loader = gateway.handler._config_loader
        apply_sections = loader.apply_process_config

        def a_rest_write_lands(full_config: dict[str, Any]) -> None:
            """Between the read when the reload was prepared and its commit."""
            apply_sections(full_config)
            store.rows = [("provider", SERVER, DENY_Z)]
            get_tool_access_resolver().set_mcp_server_policy(SERVER, DENY_Z)

        monkeypatch.setattr(loader, "apply_process_config", a_rest_write_lands)

        gateway.reload(_config(servers={SERVER: self.GOVERNED_X}))

        assert get_tool_access_resolver().get_configured_policy("provider", SERVER) == DENY_Z

    def test_a_slow_store_never_holds_up_a_policy_check(self, gateway: _Gateway, store: _PolicyStore) -> None:
        """Both reads happen with the resolver lock free, so a call is not held up by the database."""
        self._boot(gateway)
        store.delay = 0.5
        waits: list[float] = []

        def a_policy_check_during_each_read() -> None:
            for _read in range(2):  # the read when prepared, and the one before the swap
                assert store.reading.wait(10)
                store.reading.clear()
                time.sleep(0.05)
                started = time.monotonic()
                get_tool_access_resolver().is_tool_allowed(SERVER, "z", member_id=TENANT)
                waits.append(time.monotonic() - started)

        caller = threading.Thread(target=a_policy_check_during_each_read)
        caller.start()
        gateway.reload(_config(servers={SERVER: self.GOVERNED_X}))
        caller.join(10)

        assert len(waits) == 2
        assert max(waits) < 0.1, waits


class TestNoCallSeesAGap:
    def test_a_call_during_a_reload_sees_the_previous_governance(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Paused with the new configuration built and not yet in force.

        A concurrent caller probes then, on another thread. Before #1424 the
        policies, withdrawals, pins and header_exposure blocks had all been
        cleared by this point, and so had the group registry, so a member was
        checked as a standalone server and its group's rules did not apply.
        """
        servers = {SERVER: GOVERNED, "m1": _server(), "g": GROUP}
        gateway.boot(_config(servers=servers))
        built, probed = threading.Event(), threading.Event()
        seen: dict[str, Any] = {}
        original = server_config._load_mcp_server_config

        def load_then_pause(mcp_server_id: str, spec: dict[str, Any]) -> Any:
            loaded = original(mcp_server_id, spec)
            if mcp_server_id == "late":  # the last server in the file
                built.set()
                assert probed.wait(10)
            return loaded

        def a_concurrent_call() -> None:
            assert built.wait(10)
            seen.update(_governance(SERVER))
            seen.update(_group_rules())
            seen["late_is_served"] = get_runtime().repository.get("late") is not None
            probed.set()

        monkeypatch.setattr(server_config, "_load_mcp_server_config", load_then_pause)
        caller = threading.Thread(target=a_concurrent_call)
        caller.start()

        gateway.reload(_config(servers={**servers, "late": _server(tools={"deny_list": ["x"]})}))
        caller.join(10)

        assert seen == {
            "denies_x": True,
            "withdrawn": True,
            "pinned": True,
            "header_exposure": True,
            "y_allowed": False,
            "gw_allowed": False,
            # Not in the repository before its policy is in force.
            "late_is_served": False,
        }
        assert get_runtime().repository.get("late") is not None
        assert not get_tool_access_resolver().is_tool_allowed("late", "x")
        assert _group_rules() == {"y_allowed": False, "gw_allowed": False}

    def test_a_reload_that_fails_while_building_changes_nothing(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One edit deletes every process-wide section and breaks one server block.

        The block is refused only when the servers are built. That used to
        happen after the servers were stopped and the sections applied, so the
        refused reload had already removed the validators and emptied the
        group registry (#1424).
        """
        servers = {SERVER: GOVERNED, "m1": _server(), "g": GROUP}
        gateway.boot(_config(servers=servers, **{section: spec[0] for section, spec in SECTIONS.items()}))
        sections = _process_state()
        repository = get_runtime().repository
        running = {mcp_server_id: repository.get(mcp_server_id) for mcp_server_id in (SERVER, "m1")}
        group = GROUPS["g"]
        _, shutdown = _spy_on_shutdown(monkeypatch, SERVER)
        broken = _server(access={"prompt": {"approval_list": ["draft_*"]}})

        with pytest.raises(ConfigurationError, match="approval_list"):
            gateway.reload(_config(servers={**servers, "bad": broken}))

        assert _process_state() == sections
        assert all(repository.get(mcp_server_id) is server for mcp_server_id, server in running.items())
        assert repository.get("bad") is None
        shutdown.assert_not_called()
        assert GROUPS["g"] is group
        assert set(_governance(SERVER).values()) == {True}
        assert _group_rules() == {"y_allowed": False, "gw_allowed": False}


#: A top-level server a group names as its member, never started.
TOP_LEVEL = {"mode": "subprocess", "command": ["python", "-c", "pass"], "description": "top-level"}


def _pool(*members: dict[str, Any]) -> dict[str, Any]:
    return {"mode": "group", "auto_start": False, "members": list(members) or [{"id": "m1"}]}


def _pool_member_is_the_repository_server() -> bool:
    member = GROUPS["pool"].get_member("m1")
    return member is not None and member.mcp_server is get_runtime().repository.get("m1")


class TestAGroupMemberIsItsTopLevelServerWhateverTheOrder:
    """A group listed before its member's server holds that server, after a reload too (#1437)."""

    def test_a_group_before_its_server_holds_the_repository_server_after_a_reload(self, gateway: _Gateway) -> None:
        config = _config(servers={"pool": _pool(), "m1": TOP_LEVEL})
        gateway.boot(config)
        booted = get_runtime().repository.get("m1")
        assert _pool_member_is_the_repository_server()

        result = gateway.reload(config)

        assert result["mcp_servers_unchanged"] == ["m1"]
        assert get_runtime().repository.get("m1") is booted
        assert _pool_member_is_the_repository_server()
        assert booted.description == "top-level"

    def test_swapping_the_order_on_a_reload_replaces_nothing(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gateway.boot(_config(servers={"m1": TOP_LEVEL, "pool": _pool()}))
        booted, shutdown = _spy_on_shutdown(monkeypatch, "m1")

        result = gateway.reload(_config(servers={"pool": _pool(), "m1": TOP_LEVEL}))

        assert result["mcp_servers_unchanged"] == ["m1"]
        shutdown.assert_not_called()
        assert get_runtime().repository.get("m1") is booted
        assert _pool_member_is_the_repository_server()

    def test_an_inline_entry_under_a_later_server_id_leaves_no_second_copy(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The group held a copy the repository never did, so a reload never stopped it."""
        config = _config(servers={"pool": _pool({"id": "m1", **_server()}), "m1": TOP_LEVEL})
        gateway.boot(config)
        assert _pool_member_is_the_repository_server()
        booted, shutdown = _spy_on_shutdown(monkeypatch, "m1")

        result = gateway.reload(config)

        assert result["mcp_servers_unchanged"] == ["m1"]
        shutdown.assert_not_called()
        assert get_runtime().repository.get("m1") is booted
        assert _pool_member_is_the_repository_server()
        assert booted.description == "top-level"

    def test_a_reload_whose_member_names_no_server_is_refused_and_changes_nothing(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gateway.boot(_config(servers={"m1": TOP_LEVEL, "pool": _pool()}))
        booted, shutdown = _spy_on_shutdown(monkeypatch, "m1")
        pool = GROUPS["pool"]

        with pytest.raises(ConfigurationError, match="Group 'pool' member 'late' names no server"):
            gateway.reload(_config(servers={"m1": TOP_LEVEL, "pool": _pool({"id": "m1"}, {"id": "late"})}))

        shutdown.assert_not_called()
        assert get_runtime().repository.get("m1") is booted
        assert get_runtime().repository.get("late") is None
        assert GROUPS["pool"] is pool


#: One server of each kind, none of them setting `resources`: the files #1426 is about.
KINDS: dict[str, dict[str, Any]] = {
    SERVER: _server(),
    "docker": {"mode": "docker", "image": "example/server:1"},
    "remote": {"mode": "remote", "endpoint": "http://127.0.0.1:9/mcp"},
}

#: One server setting changed on one server: each is something the server is built from.
ONE_SETTING: list[tuple[str, dict[str, Any]]] = [
    (SERVER, {"command": ["python", "-c", "pass  # edited"]}),
    (SERVER, {"args": ["--verbose"]}),
    (SERVER, {"env": {"LEVEL": "debug"}}),
    (SERVER, {"resources": {"memory": "1g", "cpu": "1.0"}}),
    (SERVER, {"user": "1000:1000"}),
    (SERVER, {"read_only": False}),
    (SERVER, {"network": "bridge"}),
    (SERVER, {"volumes": ["/tmp/data:/data:ro"]}),
    (SERVER, {"description": "edited"}),
    (SERVER, {"idle_ttl_s": 30}),
    (SERVER, {"health_check_interval_s": 5}),
    (SERVER, {"max_consecutive_failures": 9}),
    (SERVER, {"tools": [{"name": "listed", "description": "a predefined tool"}]}),
    (SERVER, {"capabilities": {"network": {"dns_allowed": False}}}),
    ("docker", {"image": "example/server:2"}),
    ("docker", {"build": {"context": "."}}),
    ("remote", {"endpoint": "http://127.0.0.1:10/mcp"}),
    ("remote", {"auth": {"type": "bearer", "token": "t"}}),
    ("remote", {"tls": {"verify_ssl": False}}),
    ("remote", {"http": {"connect_timeout": 5}}),
]


class TestOnlyAChangedServerRestarts:
    """A reload restarts a server only when the file changes what the server is built from (#1426).

    The diff ran a check of its own on part of the spec, and counted a server
    whose file left `resources` out as changed on every reload. It stopped the
    server, and the commit put the stopped server back.
    """

    def _boot(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch, servers: dict[str, Any]
    ) -> dict[str, tuple[Any, Mock]]:
        gateway.boot(_config(servers=servers))
        return {mcp_server_id: _spy_on_shutdown(monkeypatch, mcp_server_id) for mcp_server_id in servers}

    def test_an_unchanged_file_restarts_nothing_of_any_kind(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        running = self._boot(gateway, monkeypatch, KINDS)

        result = gateway.reload(_config(servers=KINDS))

        assert result["mcp_servers_updated"] == []
        assert result["mcp_servers_unchanged"] == sorted(KINDS)
        for mcp_server_id, (server, shutdown) in running.items():
            shutdown.assert_not_called()
            assert get_runtime().repository.get(mcp_server_id) is server

    def test_a_default_left_out_or_spelled_out_is_the_same_server(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ((server, shutdown),) = self._boot(gateway, monkeypatch, {SERVER: _server()}).values()
        spelled_out = _server(
            env={},
            idle_ttl_s=300,
            health_check_interval_s=60,
            max_consecutive_failures=3,
            volumes=[],
            resources={"memory": "512m", "cpu": "1.0"},
            network="none",
            read_only=True,
        )

        result = gateway.reload(_config(servers={SERVER: spelled_out}))

        assert result["mcp_servers_unchanged"] == [SERVER]
        shutdown.assert_not_called()
        assert get_runtime().repository.get(SERVER) is server

    @pytest.mark.parametrize(
        ("changed", "edit"), ONE_SETTING, ids=[f"{sid}.{next(iter(edit))}" for sid, edit in ONE_SETTING]
    )
    def test_one_changed_setting_restarts_only_that_server(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch, changed: str, edit: dict[str, Any]
    ) -> None:
        running = self._boot(gateway, monkeypatch, KINDS)

        result = gateway.reload(_config(servers={**KINDS, changed: {**KINDS[changed], **edit}}))

        assert result["mcp_servers_updated"] == [changed]
        assert result["mcp_servers_unchanged"] == sorted(set(KINDS) - {changed})
        for mcp_server_id, (server, shutdown) in running.items():
            assert shutdown.call_count == (1 if mcp_server_id == changed else 0), mcp_server_id
            assert (get_runtime().repository.get(mcp_server_id) is server) is (mcp_server_id != changed)

    def test_governance_alone_restarts_nothing_and_still_takes_effect(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Policies, withdrawals, pins and header_exposure are swapped in whether or not the server is kept."""
        ((server, shutdown),) = self._boot(gateway, monkeypatch, {SERVER: _server()}).values()
        governed = {
            **GOVERNED,
            "access": {"prompt": {"deny_list": ["draft_*"]}},
            "tool_access": {"member": {TENANT: {"deny_list": ["y"]}}},
        }

        declared = gateway.reload(_config(servers={SERVER: governed}))
        in_force = _governance(SERVER)
        lifted = gateway.reload(_config(servers={SERVER: _server()}))

        assert declared["mcp_servers_unchanged"] == lifted["mcp_servers_unchanged"] == [SERVER]
        assert set(in_force.values()) == {True}
        assert set(_governance(SERVER).values()) == {False}
        shutdown.assert_not_called()
        assert get_runtime().repository.get(SERVER) is server

    def test_an_inline_member_keeps_running_through_a_new_weight_and_restarts_on_a_new_env(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def pool(**member: Any) -> dict[str, Any]:
            return {"g": {"mode": "group", "auto_start": False, "members": [{"id": "m1", **_server(), **member}]}}

        gateway.boot(_config(servers=pool(weight=1)))
        server, shutdown = _spy_on_shutdown(monkeypatch, "m1")

        reweighted = gateway.reload(_config(servers=pool(weight=2, priority=3)))

        member = GROUPS["g"].get_member("m1")
        assert reweighted["mcp_servers_unchanged"] == ["m1"]
        shutdown.assert_not_called()
        assert member is not None and member.mcp_server is server and member.weight == 2

        edited = gateway.reload(_config(servers=pool(weight=2, priority=3, env={"LEVEL": "debug"})))

        member = GROUPS["g"].get_member("m1")
        assert edited["mcp_servers_updated"] == ["m1"]
        shutdown.assert_called_once()
        assert member is not None and member.mcp_server is get_runtime().repository.get("m1")
        assert member.mcp_server is not server


class TestARuntimeL7PolicyAcrossAReload:
    """A policy set over the API lives on the server object, and no file declares one (#1498)."""

    @staticmethod
    def _set_over_the_api(mcp_server_id: str, policy: L7Policy | None) -> None:
        """What `POST /api/mcp_servers/{id}/l7_policy` does: the REST handler's own command."""
        SetL7PolicyHandler(get_runtime().repository, Mock()).handle(
            SetL7PolicyCommand(mcp_server_id=mcp_server_id, policy=policy, source="operator")
        )

    def test_a_rebuilt_server_keeps_it(self, gateway: _Gateway) -> None:
        gateway.boot(_config(servers={SERVER: _server(env={"TOKEN": "before"})}))
        repository = get_runtime().repository
        before = repository.get(SERVER)
        policy = L7Policy(tools=ToolRules(deny=("delete_*",)))
        self._set_over_the_api(SERVER, policy)

        result = gateway.reload(_config(servers={SERVER: _server(env={"TOKEN": "after"})}))

        after = repository.get(SERVER)
        assert result["mcp_servers_updated"] == [SERVER], "the new `env` rebuilds it"
        assert after is not before
        assert after.l7_policy == policy

    def test_a_kept_server_keeps_it(self, gateway: _Gateway) -> None:
        config = _config(servers={SERVER: _server()})
        gateway.boot(config)
        repository = get_runtime().repository
        before = repository.get(SERVER)
        policy = L7Policy(tools=ToolRules(deny=("delete_*",)))
        self._set_over_the_api(SERVER, policy)

        result = gateway.reload(config)

        assert result["mcp_servers_unchanged"] == [SERVER]
        assert repository.get(SERVER) is before
        assert before.l7_policy is policy
