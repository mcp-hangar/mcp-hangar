"""The servers a client already has go into the Hangar config, started and pinned (#1770, stage 2).

Stage 1 read them. Here each is started once, exactly as `serve` would run it,
its tools are pinned while it is up, and only the servers that answered are
written. One that does not answer stays in its client as it was and is named
in the report (maintainer decision on #1770). Secrets in `env` are copied
verbatim, and the config that holds them is readable by its owner only.

The happy path starts real stdio servers (the math example), so the pins are
real digests and `pin --check` can be asked straight afterwards.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest
import yaml

from mcp_hangar.server.cli.services.client_import import hangar_entry, import_client_servers
from mcp_hangar.server.cli.services.client_servers import (
    ClientServers,
    EntryProblem,
    RemoteServer,
    StdioServer,
    read_client_servers,
)
from mcp_hangar.server.cli.services.mcp_clients import McpClient
from mcp_hangar.server.cli.services.pinning import Observation, find_drift, observe_digests

MATH = Path(__file__).resolve().parents[3] / "examples" / "provider_math" / "server.py"
SECRET = "not-a-real-token-imported"


def _project(tmp_path: Path, servers: dict[str, object]) -> ClientServers:
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    client = McpClient(key="claude-code-project", label="Claude Code (project)", path=path, declares_type=True)
    return read_client_servers(client)


def _math(**env: str) -> dict[str, object]:
    return {"command": sys.executable, "args": [str(MATH)], "env": {"MCP_TRANSPORT": "stdio", **env}}


class TestThreeStdioServers:
    """The acceptance case: three stdio servers in a project `.mcp.json`."""

    @pytest.fixture
    def imported(self, tmp_path: Path):
        config = tmp_path / "config.yaml"
        source = _project(tmp_path, {"alpha": _math(), "beta": _math(TOKEN=SECRET), "gamma": _math()})
        return config, import_client_servers(config, [source], timeout_s=120)

    def test_each_is_written_under_its_own_name_with_its_args_and_env(self, imported) -> None:
        config, report = imported
        servers = yaml.safe_load(config.read_text())["mcp_servers"]

        assert [i.name for i in report.imported] == ["alpha", "beta", "gamma"]
        assert report.not_imported == []
        assert servers["beta"]["command"] == [sys.executable]
        assert servers["beta"]["args"] == [str(MATH)]
        assert servers["beta"]["env"]["TOKEN"] == SECRET, "secrets are copied verbatim (decision 3)"

    def test_every_tool_is_pinned_and_pin_check_finds_nothing(self, imported) -> None:
        config, report = imported
        servers = yaml.safe_load(config.read_text())["mcp_servers"]

        assert all(i.tools > 0 for i in report.imported)
        for name in ("alpha", "beta", "gamma"):
            assert len(servers[name]["tool_projection"]["pins"]) == report.imported[0].tools
        observations = observe_digests(config, timeout_s=120)
        assert all(o.ok for o in observations), [o.error for o in observations]
        assert find_drift(yaml.safe_load(config.read_text()), observations) == []

    def test_a_config_holding_env_is_readable_by_its_owner_only(self, imported) -> None:
        config, _ = imported
        assert stat.S_IMODE(config.stat().st_mode) == 0o600


def _answers(digests: dict[str, dict[str, str]]):
    """An `observe` that answers for the named servers and fails the rest."""

    def observe(name: str, spec: dict[str, object], timeout_s: float) -> Observation:
        if name in digests:
            return Observation(name, digests=digests[name], upstream=f"stub:{name}")
        return Observation(name, error="exited before it answered")

    return observe


class TestWhatIsLeftWhereItWas:
    def test_a_server_that_does_not_start_is_not_written_and_is_named(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        source = _project(tmp_path, {"works": {"command": "a"}, "broken": {"command": "b"}})

        report = import_client_servers(config, [source], observe=_answers({"works": {"t": "d"}}))

        assert list(yaml.safe_load(config.read_text())["mcp_servers"]) == ["works"]
        (left,) = report.not_imported
        assert (left.name, left.client_key) == ("broken", "claude-code-project")
        assert "did not start" in left.reason

    def test_nothing_is_written_when_nothing_starts(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        report = import_client_servers(config, [_project(tmp_path, {"broken": {"command": "b"}})], observe=_answers({}))

        assert report.imported == []
        assert not config.exists()

    def test_a_missing_environment_variable_is_a_reason_not_a_crash(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("HANGAR_TEST_UNSET_VAR", raising=False)
        config = tmp_path / "config.yaml"
        source = _project(tmp_path, {"needs": {"command": "a", "env": {"K": "${HANGAR_TEST_UNSET_VAR}"}}})

        report = import_client_servers(config, [source], observe=_answers({"needs": {"t": "d"}}))

        (left,) = report.not_imported
        assert "HANGAR_TEST_UNSET_VAR" in left.reason

    def test_an_opted_out_server_is_left_alone(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        source = _project(tmp_path, {"keep": {"command": "a"}, "take": {"command": "b"}})

        report = import_client_servers(
            config,
            [source],
            skip=frozenset({("claude-code-project", "keep")}),
            observe=_answers({"keep": {"t": "d"}, "take": {"t": "d"}}),
        )

        assert [i.name for i in report.imported] == ["take"]
        assert report.not_imported[0].reason == "left out at your request"

    def test_a_name_the_config_already_has_is_not_overwritten(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        config.write_text(yaml.safe_dump({"mcp_servers": {"math": {"mode": "subprocess", "command": ["mine"]}}}))
        source = _project(tmp_path, {"math": {"command": "theirs"}})

        report = import_client_servers(config, [source], observe=_answers({"math": {"t": "d"}}))

        assert yaml.safe_load(config.read_text())["mcp_servers"]["math"]["command"] == ["mine"]
        assert "already has a server with this name" in report.not_imported[0].reason

    def test_one_name_in_two_clients_is_imported_once_or_refused_when_they_differ(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        same = _project(tmp_path / "a", {"shared": {"command": "x"}, "clash": {"command": "one"}})
        other = _project(tmp_path / "b", {"shared": {"command": "x"}, "clash": {"command": "two"}})

        report = import_client_servers(config, [same, other], observe=_answers({"shared": {}, "clash": {}}))

        assert sorted(i.name for i in report.imported) == ["clash", "shared"]
        (refused,) = report.not_imported
        assert refused.name == "clash" and "different server under this name" in refused.reason


class TestTheReport:
    def test_stage_one_problems_are_carried_into_the_report(self, tmp_path: Path) -> None:
        source = _project(tmp_path, {"bad": {"command": 5}})
        report = import_client_servers(tmp_path / "config.yaml", [source], observe=_answers({}))
        assert [p.entry for p in report.problems] == ["bad"]
        assert isinstance(report.problems[0], EntryProblem)

    def test_no_reason_quotes_a_secret(self, tmp_path: Path) -> None:
        source = _project(tmp_path, {"leaky": {"command": "a", "env": {"TOKEN": SECRET}}})
        report = import_client_servers(tmp_path / "config.yaml", [source], observe=_answers({}))
        assert SECRET not in repr(report)

    def test_the_previous_config_is_kept(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        config.write_text("mcp_servers: {}\n")
        source = _project(tmp_path, {"s": {"command": "a"}})
        report = import_client_servers(config, [source], observe=_answers({"s": {}}))
        assert report.backup is not None and report.backup.read_text() == "mcp_servers: {}\n"


class TestTheEntry:
    def test_a_stdio_server(self) -> None:
        entry = hangar_entry(StdioServer("s", "npx", ("-y", "pkg"), {"K": "${K}"}))
        assert entry == {"mode": "subprocess", "command": ["npx"], "args": ["-y", "pkg"], "env": {"K": "${K}"}}

    def test_a_remote_server_keeps_its_headers(self) -> None:
        entry = hangar_entry(RemoteServer("r", "https://mcp.example.com/mcp", "http", {"Authorization": "Bearer t"}))
        assert entry == {
            "mode": "remote",
            "endpoint": "https://mcp.example.com/mcp",
            "http": {"headers": {"Authorization": "Bearer t"}},
        }
