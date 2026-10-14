"""The README quickstart runner picks exactly the marked blocks (#1774)."""

from __future__ import annotations

import importlib.util
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


def test_the_readme_has_a_quickstart_to_run() -> None:
    blocks = readme_quickstart.quickstart_blocks((ROOT / "README.md").read_text(encoding="utf-8"))

    assert len(blocks) == 2
    assert "export PATH" in blocks[0]
