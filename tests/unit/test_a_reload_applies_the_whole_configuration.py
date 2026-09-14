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
import threading
from typing import Any
from collections.abc import Callable
from unittest.mock import Mock

import pytest
import yaml

from mcp_hangar.application.commands import ReloadConfigurationCommand
from mcp_hangar.application.commands.reload_handler import ReloadConfigurationHandler
from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.events import ConfigurationReloadFailed
from mcp_hangar.domain.exceptions import ConfigurationError, ConfigurationRestartRequiredError
from mcp_hangar.domain.policies.header_exposure import clear_header_exposure_policies, get_header_exposure_policy
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.services.ui_resource_guard import get_ui_resource_guard, reset_ui_resource_guard
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.fastmcp_server import flat_tool_projection
from mcp_hangar.fastmcp_server import resource_link_read_through as rt
from mcp_hangar.server import config as server_config
from mcp_hangar.server.api import middleware
from mcp_hangar.server.config import load_configuration, ServerConfigLoader
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
IDS = (SERVER, "extra", "late", "other", "m1")


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


class TestNoCallSeesAGap:
    def test_a_call_during_a_reload_sees_the_previous_governance(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Paused with the new configuration built and not yet in force.

        A concurrent caller probes then, on another thread. Before #1424 the
        policies, withdrawals, pins and header_exposure blocks had all been
        cleared by this point, so every probe below read False.
        """
        gateway.boot(_config(servers={SERVER: GOVERNED}))
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
            seen["late_is_served"] = get_runtime().repository.get("late") is not None
            probed.set()

        monkeypatch.setattr(server_config, "_load_mcp_server_config", load_then_pause)
        caller = threading.Thread(target=a_concurrent_call)
        caller.start()

        gateway.reload(_config(servers={SERVER: GOVERNED, "late": _server(tools={"deny_list": ["x"]})}))
        caller.join(10)

        assert seen == {
            "denies_x": True,
            "withdrawn": True,
            "pinned": True,
            "header_exposure": True,
            # Not in the repository before its policy is in force.
            "late_is_served": False,
        }
        assert get_runtime().repository.get("late") is not None
        assert not get_tool_access_resolver().is_tool_allowed("late", "x")

    def test_a_reload_that_fails_while_building_leaves_the_governance_in_force(self, gateway: _Gateway) -> None:
        gateway.boot(_config(servers={SERVER: GOVERNED}))

        with pytest.raises(ConfigurationError, match="approval_list"):
            gateway.reload(_config(servers={SERVER: _server(access={"prompt": {"approval_list": ["draft_*"]}})}))

        assert set(_governance(SERVER).values()) == {True}
