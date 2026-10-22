"""`init` moves the servers a client already has behind Hangar (#1770, stage 3).

Driven through the CLI against a fixture machine with all three detected
clients:
- a project `.mcp.json` with three stdio servers (`tests/fixtures/named_tool_server.py`);
- a Claude Code `~/.claude.json` with one;
- a Cursor `~/.cursor/mcp.json` with one server that cannot start.

After `init -y`:
- each client file that had servers which started holds only the
  `mcp-hangar` entry;
- the one that cannot start stays in Cursor, as it was, and the summary
  names it;
- the Hangar config holds the servers that moved, every tool pinned, and no
  default bundle;
- the config is owner-only, because it holds `env`;
- `tools/list` through the client's entry shows the moved servers' tools.
"""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from mcp_hangar.server.cli.commands import init as init_module
from mcp_hangar.server.cli.main import app
from mcp_hangar.server.cli.services.dependency_detector import DependencyStatus, RuntimeInfo
from mcp_hangar.server.cli.services.mcp_clients import HANGAR_ENTRY_NAME
from mcp_hangar.server.cli.services.pinning import find_drift, observe_digests

SERVER = Path(__file__).resolve().parents[2] / "fixtures" / "named_tool_server.py"
SECRET = "not-a-real-token-moved"
NPX_ONLY = DependencyStatus(
    npx=RuntimeInfo("npx", "/usr/bin/npx", True),
    uvx=RuntimeInfo("uvx", None, False),
    docker=RuntimeInfo("docker", None, False),
    podman=RuntimeInfo("podman", None, False),
)


def _server(tool: str, **env: str) -> dict[str, object]:
    """A stdio server serving one tool, *tool*; distinct names keep the front door from withholding a collision."""
    return {"command": sys.executable, "args": [str(SERVER), tool], "env": dict(env)}


def _servers(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())["mcpServers"]


@pytest.fixture(scope="module")
def machine(tmp_path_factory: pytest.TempPathFactory):
    """Run `init -y` once on a fixture machine; yield its paths and the CLI output."""
    root = tmp_path_factory.mktemp("machine")
    home, project = root / "home", root / "project"
    (home / ".cursor").mkdir(parents=True)
    project.mkdir()
    (project / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "alpha": _server("alpha_echo"),
                    "beta": _server("beta_echo", TOKEN=SECRET),
                    "gamma": _server("gamma_echo"),
                }
            }
        )
    )
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"delta": _server("delta_echo")}, "theme": "dark"}))
    (home / ".cursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"broken": {"command": str(root / "no-such-server")}}})
    )
    config = root / "hangar.yaml"

    patch = pytest.MonkeyPatch()
    patch.setenv("HOME", str(home))
    patch.delenv("MCP_CONFIG", raising=False)
    patch.chdir(project)
    patch.setattr(init_module, "detect_dependencies", lambda: NPX_ONLY)
    try:
        result = CliRunner().invoke(app, ["init", "-y", "--config-path", str(config)])
    finally:
        patch.undo()
    yield {"home": home, "project": project, "config": config, "result": result}


def test_init_succeeds(machine) -> None:
    assert machine["result"].exit_code == 0, machine["result"].output


def test_each_client_whose_servers_moved_holds_only_the_hangar_entry(machine) -> None:
    assert list(_servers(machine["project"] / ".mcp.json")) == [HANGAR_ENTRY_NAME]
    assert list(_servers(machine["home"] / ".claude.json")) == [HANGAR_ENTRY_NAME]
    assert json.loads((machine["home"] / ".claude.json").read_text())["theme"] == "dark", "the rest of the file is kept"


def test_a_server_that_does_not_start_stays_where_it_was_and_is_named(machine) -> None:
    cursor = _servers(machine["home"] / ".cursor" / "mcp.json")
    assert set(cursor) == {"broken", HANGAR_ENTRY_NAME}
    output = " ".join(machine["result"].output.split())
    assert "broken" in output and "did not start" in output


def test_the_hangar_config_has_the_moved_servers_and_no_default_bundle(machine) -> None:
    servers = yaml.safe_load(machine["config"].read_text())["mcp_servers"]
    assert sorted(servers) == ["alpha", "beta", "delta", "gamma"]
    assert servers["beta"]["env"]["TOKEN"] == SECRET
    assert all(servers[name]["tool_projection"]["pins"] for name in servers)


def test_pin_check_passes_straight_after_init(machine) -> None:
    observations = observe_digests(machine["config"], timeout_s=120)
    assert all(o.ok for o in observations), [o.error for o in observations]
    assert find_drift(yaml.safe_load(machine["config"].read_text()), observations) == []


def test_the_config_holding_env_is_owner_only(machine) -> None:
    assert stat.S_IMODE(machine["config"].stat().st_mode) == 0o600


def test_tools_list_through_the_entry_shows_the_moved_servers_tools(machine) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    entry = _servers(machine["project"] / ".mcp.json")[HANGAR_ENTRY_NAME]

    async def tools() -> list[str]:
        params = StdioServerParameters(command=entry["command"], args=entry["args"], env={"HOME": str(machine["home"])})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return [tool.name for tool in (await session.list_tools()).tools]

    names = asyncio.run(asyncio.wait_for(tools(), timeout=120))
    assert {"alpha_echo", "beta_echo", "gamma_echo", "delta_echo"} <= set(names), names


def test_the_opt_out_prompt_leaves_the_unchecked_servers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_hangar.server.cli.services.client_servers import read_client_servers
    from mcp_hangar.server.cli.services.mcp_clients import McpClient

    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": {"keep": {"command": "a"}, "move": {"command": "b"}}}))
    found = [read_client_servers(McpClient(key="claude-code-project", label="Claude Code (project)", path=path))]

    class _Prompt:
        def __init__(self, choices):
            self.choices = choices

        def ask(self):
            return [c.value for c in self.choices if c.value[1] == "move"]

    monkeypatch.setattr(init_module.questionary, "checkbox", lambda message, choices: _Prompt(choices))

    assert init_module._prompt_import_opt_out(found) == frozenset({("claude-code-project", "keep")})


def test_with_skip_test_the_servers_stay_and_no_bundle_is_installed(tmp_path: Path, monkeypatch) -> None:
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"alpha": _server("alpha_echo")}}))
    config = tmp_path / "hangar.yaml"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("MCP_CONFIG", raising=False)
    monkeypatch.chdir(project)
    monkeypatch.setattr(init_module, "detect_dependencies", lambda: NPX_ONLY)

    result = CliRunner().invoke(app, ["init", "-y", "--skip-test", "--config-path", str(config)])

    assert result.exit_code == 0, result.output
    assert set(_servers(project / ".mcp.json")) == {"alpha", HANGAR_ENTRY_NAME}
    assert not (yaml.safe_load(config.read_text()).get("mcp_servers") or {}), "no default bundle beside the user's own"
