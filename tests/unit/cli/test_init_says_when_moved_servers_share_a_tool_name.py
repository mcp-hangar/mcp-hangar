"""`init` says when the servers it moved serve the same tool name (#2193).

On the front door a tool is listed under its own name, and a name two servers
serve is listed for neither (`flat_tool_name_collision`). Three servers each
serving `echo` lost all three tools while `init` reported every server moved
and working.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from mcp_hangar.server.cli.commands import init as init_module
from mcp_hangar.server.cli.main import app
from mcp_hangar.server.cli.services.client_import import colliding_tools
from mcp_hangar.server.cli.services.dependency_detector import DependencyStatus, RuntimeInfo

SERVER = Path(__file__).resolve().parents[2] / "fixtures" / "named_tool_server.py"
NPX_ONLY = DependencyStatus(
    npx=RuntimeInfo("npx", "/usr/bin/npx", True),
    uvx=RuntimeInfo("uvx", None, False),
    docker=RuntimeInfo("docker", None, False),
    podman=RuntimeInfo("podman", None, False),
)


def _pinned(*tools: str) -> dict[str, object]:
    return {"mode": "subprocess", "command": ["x"], "tool_projection": {"pins": dict.fromkeys(tools, "0" * 64)}}


def test_a_tool_name_two_servers_pin_is_named_with_both() -> None:
    config = {
        "tool_access": {"mode": "front_door"},
        "mcp_servers": {"notes": _pinned("echo", "add_note"), "search": _pinned("echo"), "calendar": _pinned("today")},
    }

    assert colliding_tools(config) == {"echo": ["notes", "search"]}


def test_distinct_names_collide_nowhere() -> None:
    config = {"tool_access": {"mode": "front_door"}, "mcp_servers": {"a": _pinned("one"), "b": _pinned("two")}}

    assert colliding_tools(config) == {}


def test_only_the_flat_listing_has_collisions() -> None:
    config = {"mcp_servers": {"a": _pinned("echo"), "b": _pinned("echo")}}

    assert colliding_tools(config) == {}
    assert colliding_tools({**config, "tool_access": {"mode": "hangar_tools"}}) == {}


def test_init_names_the_tools_its_moved_servers_share(tmp_path: Path, monkeypatch) -> None:
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    server = {"command": sys.executable, "args": [str(SERVER), "echo"]}
    other = {"command": sys.executable, "args": [str(SERVER), "add_note"]}
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"notes": server, "search": server, "plan": other}}))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("MCP_CONFIG", raising=False)
    monkeypatch.chdir(project)
    monkeypatch.setattr(init_module, "detect_dependencies", lambda: NPX_ONLY)

    result = CliRunner().invoke(app, ["init", "-y", "--config-path", str(tmp_path / "hangar.yaml")])

    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert "Not listed: echo -- served by notes, search" in output
    assert "add_note" not in output.split("Not listed")[-1].split("Remove one")[0]
