"""A configuration passed to `bootstrap()` as a dict runs the way the same file does (#1415).

Only the file path applied `tool_access.mode`, `execution`,
`headers.param_validation`, `resource_links`, `interceptors` and `ui_resources`,
and only it ran the schema check. `bootstrap(config_dict=...)` merged the dict
into whatever `MCP_CONFIG` or `./config.yaml` held and applied none of them, so
a dict asking for `front_door` came up in egress, with nothing logged.

Each test here boots the real `bootstrap()` twice -- from a temporary file and
from the same dict -- with only its heavy edges stubbed: the runtime, the event
store, CQRS registration and the FastMCP server (the `bootstrap_harness` shape
from `test_approval_gate_reachability.py`). Everything that reads configuration
runs for real. The served path, a real boot and a real call, is
`tests/integration/test_a_config_dict_validator_refuses_a_served_call.py`.

The sections come from the schema. A new section fails
`test_every_section_is_set_and_probed` until someone writes down how to observe
it, and `TestTheCheckItselfFails` shows that a section only one path applies
fails the parity check.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import importlib
from pathlib import Path
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs
import yaml

from mcp_hangar.domain.contracts.validator import ValidationContext
from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.services import get_tool_access_resolver
from mcp_hangar.domain.services.ui_resource_guard import get_ui_resource_guard, reset_ui_resource_guard
from mcp_hangar.fastmcp_server import resource_link_read_through
from mcp_hangar.fastmcp_server.flat_tool_projection import param_validation_required, set_param_validation_required
from mcp_hangar.server import config_schema
from mcp_hangar.server.bootstrap import ApplicationContext
from mcp_hangar.server.config_schema import ConfigSchemaError
from mcp_hangar.server.context import get_context
from mcp_hangar.server.state import get_runtime
from mcp_hangar.server.tools import batch
from mcp_hangar.server.tools.batch.concurrency import get_concurrency_manager, reset_concurrency_manager

#: The package, not the `bootstrap` function `mcp_hangar.server` re-exports under the same name.
bootstrap_package = importlib.import_module("mcp_hangar.server.bootstrap")

SERVER = "parity-math"
TENANT = "tenant:parity"


def _server() -> dict[str, Any]:
    return {"mode": "subprocess", "command": [sys.executable, "-c", "pass"], "tools": {"deny_list": ["divide"]}}


def _config() -> dict[str, Any]:
    """Every top-level section of the schema, each set away from its default where it has one."""
    return {
        "mcp_servers": {SERVER: _server()},
        "approvals": {"enabled": False},
        "auth": {
            "enabled": True,
            "allow_anonymous": False,
            "api_key": {"enabled": True},
            "storage": {"driver": "memory"},
        },
        # Only `enabled: false`: a dict has no file to watch, and asking it to is
        # refused (`test_a_dict_that_asks_for_reload_is_refused`).
        "config_reload": {"enabled": False},
        # `coordination` is in REFUSED_ALIKE: a standalone boot cannot carry it.
        "discovery": {"enabled": False, "refresh_interval_s": 45},
        "event_store": {"enabled": False},
        "execution": {"max_concurrency": 7, "default_mcp_server_concurrency": 3},
        "headers": {"param_validation": {"required": True}},
        "hot_loading": {"enabled": False},
        "interceptors": {"validators": [{"type": "payload_size", "max_bytes": 64}]},
        "logging": {"level": "DEBUG"},
        "observability": {"tracing": {"enabled": False}},
        "persistence": {"sqlite": {"data_dir": "./parity-data"}},
        "rate_limit": {"rps": 11, "burst": 22},
        "relay_tasks_enabled": False,
        "resource_links": {"max_per_tenant": 17},
        "retry": {"default_policy": {"max_attempts": 2}},
        "startup_checks": {"enforce": True},
        "tool_access": {"mode": "front_door"},
        "truncation": {"enabled": False},
        # Interpolated on both paths: `${VAR:-default}` resolves to `reports`.
        "ui_resources": {"tenants": {TENANT: {"allowlist": ["ui://${HANGAR_PARITY_UI:-reports}/"]}}},
    }


def _verdict(size: int) -> bool:
    """Whether the executor's validators let a `tools/call` of about `size` bytes through."""
    pipeline = batch._executor._validator_pipeline
    context = ValidationContext(
        method="tools/call",
        direction="request",
        payload={"name": "add", "arguments": {"pad": "x" * size}},
        correlation_id="parity",
    )
    return pipeline.execute(context).allowed


def _from_context_config(section: str) -> Callable[[ApplicationContext], Any]:
    """A section read further into `bootstrap()`, from `context.config`.

    The readers of these sections come after the configuration is read, and
    both paths run the same ones with the same dict. Equal input to them is
    equal effect, so the dict is the observable. A section applied while the
    configuration is being read -- where the split was -- has a probe of the
    state it sets instead.
    """
    return lambda context: context.config.get(section)


#: Section -> how to read what a boot made of it.
PROBES: dict[str, Callable[[ApplicationContext], Any]] = {
    "mcp_servers": lambda context: (
        get_runtime().repository.exists(SERVER),
        get_tool_access_resolver().is_tool_allowed(SERVER, "divide"),
    ),
    "approvals": lambda context: context.approval_service is not None,
    "auth": lambda context: context.auth_components.enabled,
    "config_reload": lambda context: sorted(type(worker).__name__ for worker in context.background_workers),
    "discovery": lambda context: (context.discovery_orchestrator is not None, context.config.get("discovery")),
    "event_store": _from_context_config("event_store"),
    "execution": lambda context: (
        get_concurrency_manager().global_limit,
        get_concurrency_manager().default_mcp_server_limit,
    ),
    "headers": lambda context: param_validation_required(),
    "hot_loading": _from_context_config("hot_loading"),
    "interceptors": lambda context: {"small": _verdict(1), "large": _verdict(200)},
    # Applied by neither bootstrap path: `mcp-hangar serve` reads it from the
    # file before it calls `bootstrap()`.
    "logging": _from_context_config("logging"),
    "observability": _from_context_config("observability"),
    "persistence": _from_context_config("persistence"),
    "rate_limit": _from_context_config("rate_limit"),
    "relay_tasks_enabled": _from_context_config("relay_tasks_enabled"),
    "resource_links": lambda context: resource_link_read_through._MAX_LINKS_PER_TENANT,
    "retry": _from_context_config("retry"),
    "startup_checks": _from_context_config("startup_checks"),
    "tool_access": lambda context: get_tool_access_resolver().topology_mode,
    "truncation": _from_context_config("truncation"),
    "ui_resources": lambda context: sorted(get_ui_resource_guard().policy_for(TENANT).allowlist),
}

#: Sections a standalone gateway refuses, so what both paths must share is the
#: refusal. A `coordination:` block declares a cluster, which needs shared
#: storage and servers every replica can reach -- a PostgreSQL backend and no
#: `subprocess` server -- and neither exists in a unit test.
REFUSED_ALIKE: dict[str, Any] = {
    "coordination": {"lease_ttl_s": 31, "renew_interval_s": 7, "renew_deadline_s": 20},
}

#: What `_config()` asks of the sections the dict path used to drop. Checked on
#: both paths, so the two cannot agree by both ignoring a section.
APPLIED: dict[str, Any] = {
    "mcp_servers": (True, False),
    "auth": True,
    "execution": (7, 3),
    "headers": True,
    "interceptors": {"small": True, "large": False},
    "resource_links": 17,
    "tool_access": "front_door",
    "ui_resources": ["ui://reports/"],
}


def _reset_process_settings() -> None:
    """Put back what the configuration sets process-wide, so a boot cannot inherit it."""
    get_tool_access_resolver().reset()
    batch.configure_interceptors(None)
    set_param_validation_required(False)
    resource_link_read_through.set_max_links_per_tenant(resource_link_read_through.DEFAULT_MAX_LINKS_PER_TENANT)
    reset_ui_resource_guard()
    reset_concurrency_manager()
    repository = get_runtime().repository
    if repository.exists(SERVER):
        repository.remove(SERVER)


#: Called before every boot. `TestTheCheckItselfFails` adds to it.
RESETS: list[Callable[[], None]] = [_reset_process_settings]


@pytest.fixture
def boot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., ApplicationContext]]:
    """The real `bootstrap()`, heavy edges stubbed, fresh process-wide settings each call."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    # Nothing for the old dict path to merge in: no `./config.yaml`, no `MCP_CONFIG`.
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("MCP_CONFIG", raising=False)
    monkeypatch.delenv("HANGAR_CONFIG_STRICT", raising=False)
    monkeypatch.delenv("HANGAR_PARITY_UI", raising=False)
    # Tracing defaults to on, and its batch processor is a thread that would
    # outlive the suite. What `observability` asks for is compared from the dict.
    monkeypatch.setenv("MCP_TRACING_ENABLED", "false")

    context = get_context()
    saved = dict(vars(context))

    runtime = MagicMock()
    runtime.rate_limit_config.requests_per_second = 10
    runtime.rate_limit_config.burst_size = 100
    runtime.repository.get_all.return_value = {}
    runtime.repository.get_all_ids.return_value = []

    patches = [
        patch("mcp_hangar.server.bootstrap._ensure_data_dir", MagicMock()),
        patch("mcp_hangar.server.bootstrap.get_runtime", MagicMock(return_value=runtime)),
        patch("mcp_hangar.server.bootstrap.init_context", MagicMock()),
        patch("mcp_hangar.server.bootstrap.init_event_handlers", MagicMock()),
        patch("mcp_hangar.server.bootstrap.init_cqrs", MagicMock()),
        patch("mcp_hangar.server.bootstrap.init_saga", MagicMock()),
        patch("mcp_hangar.server.bootstrap.init_retry_config", MagicMock()),
        patch("mcp_hangar.server.bootstrap.init_event_store", MagicMock()),
        patch("mcp_hangar.server.bootstrap.init_hot_loading", MagicMock(return_value=(None, None))),
        patch("mcp_hangar.server.bootstrap.new_mcp_server", MagicMock()),
        patch("mcp_hangar.server.bootstrap.register_all_tools", MagicMock()),
        patch("mcp_hangar.server.bootstrap.register_modern_surface", MagicMock()),
        patch("mcp_hangar.server.bootstrap.create_background_workers", MagicMock(return_value=[])),
        patch("mcp_hangar.server.bootstrap.init_log_buffers", MagicMock()),
        patch("mcp_hangar.server.bootstrap.GROUPS", {}),
    ]
    for patcher in patches:
        patcher.start()

    def go(**kwargs: Any) -> ApplicationContext:
        for reset in RESETS:
            reset()
        return bootstrap_package.bootstrap(**kwargs)

    try:
        yield go
    finally:
        for patcher in patches:
            patcher.stop()
        _reset_process_settings()
        vars(context).clear()
        vars(context).update(saved)


def _write(tmp_path: Path, config: dict[str, Any]) -> str:
    """The same configuration as a file, outside the working directory."""
    path = tmp_path / "from-file" / "hangar.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return str(path)


def _assert_every_section_is_covered(config: dict[str, Any]) -> None:
    sections = set(config_schema.SECTIONS) - set(REFUSED_ALIKE)
    unprobed = sorted(sections - set(PROBES))
    assert not unprobed, f"no probe for section(s) {unprobed}: add one to PROBES that reads what a boot made of it"
    unset = sorted(sections - set(config))
    assert not unset, f"the parity config does not set section(s) {unset}"


def _assert_boots_alike(boot: Callable[..., ApplicationContext], tmp_path: Path, config: dict[str, Any]) -> dict:
    """Boot `config` from a file and from a dict; fail on every section that differs."""
    _assert_every_section_is_covered(config)
    sections = sorted(set(config_schema.SECTIONS) - set(REFUSED_ALIKE))

    from_file_context = boot(config_path=_write(tmp_path, config))
    from_file = {section: PROBES[section](from_file_context) for section in sections}
    from_dict_context = boot(config_dict=config)
    from_dict = {section: PROBES[section](from_dict_context) for section in sections}

    diverged = {
        section: (from_file[section], from_dict[section])
        for section in sections
        if from_file[section] != from_dict[section]
    }
    assert not diverged, f"section(s) a file and a dict apply differently, as (file, dict): {diverged}"
    return from_file


def test_every_section_is_set_and_probed() -> None:
    _assert_every_section_is_covered(_config())


def test_a_dict_and_a_file_boot_alike(boot, tmp_path) -> None:
    applied = _assert_boots_alike(boot, tmp_path, _config())

    # Alike because both applied it, not because both dropped it.
    assert {section: applied[section] for section in APPLIED} == APPLIED


@pytest.mark.parametrize("section", sorted(REFUSED_ALIKE))
def test_a_section_a_standalone_boot_refuses_is_refused_alike(boot, tmp_path, section) -> None:
    config = {**_config(), section: REFUSED_ALIKE[section]}

    with pytest.raises(Exception) as from_file:  # noqa: PT011 -- whichever refusal it is, it must be the same one
        boot(config_path=_write(tmp_path, config))
    with pytest.raises(Exception) as from_dict:  # noqa: PT011
        boot(config_dict=config)

    assert (type(from_dict.value), str(from_dict.value)) == (type(from_file.value), str(from_file.value))


def test_a_dict_is_not_merged_into_the_file_in_the_working_directory(boot, tmp_path) -> None:
    """The dict path read `./config.yaml` and applied *its* topology, then laid the dict over the rest."""
    Path("config.yaml").write_text(
        yaml.safe_dump({"mcp_servers": {"from-cwd": _server()}, "tool_access": {"mode": "front_door"}}),
        encoding="utf-8",
    )

    context = boot(config_dict={"mcp_servers": {SERVER: _server()}, "config_reload": {"enabled": False}})

    assert get_tool_access_resolver().topology_mode == "egress"
    assert set(context.config["mcp_servers"]) == {SERVER}


class TestTheSchemaCheck:
    CONFIG: dict[str, Any] = {
        "mcp_servers": {SERVER: _server()},
        "config_reload": {"enabled": False},
        "execution": {"max_concurency": 3},
        "authh": {"enabled": True},
    }

    def test_the_same_unknown_keys_are_warned_about_on_both_paths(self, boot, tmp_path) -> None:
        with capture_logs() as logs:
            boot(config_path=_write(tmp_path, self.CONFIG))
        from_file = [entry["detail"] for entry in logs if entry["event"] == "unknown_config_key"]

        with capture_logs() as logs:
            boot(config_dict=self.CONFIG)
        from_dict = [entry["detail"] for entry in logs if entry["event"] == "unknown_config_key"]

        assert len(from_file) == 2
        assert from_dict == from_file

    def test_strict_mode_refuses_on_both_paths(self, boot, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("HANGAR_CONFIG_STRICT", "1")

        with pytest.raises(ConfigSchemaError) as from_file:
            boot(config_path=_write(tmp_path, self.CONFIG))
        with pytest.raises(ConfigSchemaError) as from_dict:
            boot(config_dict=self.CONFIG)

        # The first line names the source -- a path, or `config_dict` -- and the rest are the keys.
        assert str(from_dict.value).splitlines()[0] == "Invalid configuration in config_dict:"
        assert str(from_dict.value).splitlines()[1:] == str(from_file.value).splitlines()[1:]

    def test_a_config_with_no_servers_is_refused_on_both_paths(self, boot, tmp_path) -> None:
        config = {"tool_access": {"mode": "front_door"}}

        with pytest.raises(ValueError, match="missing 'mcp_servers'"):
            boot(config_path=_write(tmp_path, config))
        with pytest.raises(ValueError, match="missing 'mcp_servers' section in config_dict"):
            boot(config_dict=config)


class TestWhatOnlyAFileHas:
    def test_a_dict_that_asks_for_reload_is_refused(self, boot) -> None:
        with pytest.raises(ConfigurationError, match="config_reload"):
            boot(config_dict={"mcp_servers": {SERVER: _server()}, "config_reload": {"interval_s": 3}})

    def test_a_dict_that_does_not_mention_reload_still_boots(self, boot) -> None:
        context = boot(config_dict={"mcp_servers": {SERVER: _server()}})

        assert context.config["mcp_servers"] == {SERVER: _server()}

    def test_a_path_and_a_dict_together_are_refused(self, boot, tmp_path) -> None:
        # The dict ran and reload watched the file, so the first reload swapped the fleet.
        config = {"mcp_servers": {SERVER: _server()}}

        with pytest.raises(ValueError, match="not both"):
            boot(config_path=_write(tmp_path, config), config_dict=config)

    def test_the_callers_dict_is_not_changed(self, boot, monkeypatch) -> None:
        monkeypatch.setenv("HANGAR_PARITY_UI", "dash")
        config = {
            "mcp_servers": {SERVER: _server()},
            "config_reload": {"enabled": False},
            "ui_resources": {"tenants": {TENANT: {"allowlist": ["ui://${HANGAR_PARITY_UI}/"]}}},
        }

        boot(config_dict=config)

        assert config["ui_resources"]["tenants"][TENANT]["allowlist"] == ["ui://${HANGAR_PARITY_UI}/"]
        assert sorted(get_ui_resource_guard().policy_for(TENANT).allowlist) == ["ui://dash/"]


class TestTheCheckItselfFails:
    """What a regression looks like to the parity check, with a section made up for the purpose."""

    def test_a_section_with_no_probe_fails_it(self, monkeypatch) -> None:
        monkeypatch.setitem(config_schema.SECTIONS, "made_up", frozenset({"value"}))

        with pytest.raises(AssertionError, match="no probe for section.*made_up"):
            _assert_every_section_is_covered({**_config(), "made_up": {"value": 1}})

    def test_a_section_only_the_file_path_applies_fails_it(self, boot, tmp_path, monkeypatch) -> None:
        applied: dict[str, Any] = {}
        monkeypatch.setitem(config_schema.SECTIONS, "made_up", frozenset({"value"}))
        monkeypatch.setitem(PROBES, "made_up", lambda context: applied.get("value"))
        monkeypatch.setattr(sys.modules[__name__], "RESETS", [*RESETS, applied.clear])

        # The split #1415 removed, put back for one section: the file loader
        # applies it, and a dict never reaches that loader.
        read_the_file = bootstrap_package.load_configuration

        def and_apply_made_up(config_path: str | None, *, load_servers: bool) -> dict[str, Any]:
            full_config = read_the_file(config_path, load_servers=load_servers)
            applied["value"] = full_config["made_up"]["value"]
            return full_config

        monkeypatch.setattr(bootstrap_package, "load_configuration", and_apply_made_up)

        with pytest.raises(AssertionError, match=r"made_up.*\(1, None\)"):
            _assert_boots_alike(boot, tmp_path, {**_config(), "made_up": {"value": 1}})
