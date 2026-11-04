"""The README quickstart runner picks exactly the marked blocks and checks, in order (#1774, #2182)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("readme_quickstart", ROOT / "scripts" / "readme_quickstart.py")
assert _spec and _spec.loader
readme_quickstart = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(readme_quickstart)


def test_only_marked_bash_blocks_are_run() -> None:
    text = "\n".join(
        [
            "<!-- ci:quickstart -->",
            "```bash",
            "echo one",
            "```",
            "",
            "```bash",
            "echo unmarked",
            "```",
            "<!-- ci:quickstart -->",
            "```yaml",
            "not: bash",
            "```",
            "<!-- ci:quickstart -->",
            "```bash",
            "echo two",
            "```",
        ]
    )

    assert readme_quickstart.quickstart_blocks(text) == ["echo one\n", "echo two\n"]


def test_a_check_line_takes_its_place_between_the_blocks() -> None:
    text = "\n".join(
        [
            "<!-- ci:quickstart -->",
            "```bash",
            "echo one",
            "```",
            "<!-- ci:check served -->",
            "<!-- ci:quickstart -->",
            "```bash",
            "echo two",
            "```",
            "<!-- ci:check withheld -->",
        ]
    )

    assert readme_quickstart.quickstart_steps(text) == [
        ("run", "echo one\n"),
        ("check", "served"),
        ("run", "echo two\n"),
        ("check", "withheld"),
    ]


def test_the_readme_installs_inits_and_drifts_between_the_two_checks() -> None:
    steps = readme_quickstart.quickstart_steps((ROOT / "README.md").read_text(encoding="utf-8"))
    kinds = [kind if kind == "run" else body for kind, body in steps]

    assert kinds == ["run", "run", "run", "served", "run", "withheld"]
    assert "export PATH" in steps[0][1]
    assert "mcp-hangar init -y" in steps[2][1]
    assert "RUG_DESC" in steps[4][1]


def test_the_fixture_is_two_servers_of_the_readers_own(tmp_path: Path) -> None:
    readme_quickstart.seed_fixture(tmp_path, tmp_path / "home")

    servers = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert set(servers) == set(readme_quickstart.FIXTURE_TOOLS)
    assert readme_quickstart.DRIFTED not in readme_quickstart.FIXTURE_TOOLS.values()
