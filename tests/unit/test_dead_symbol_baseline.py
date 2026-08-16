"""The dead-symbol baseline may shrink. It may not grow.

Five times this month a defect turned out to be code that could not run -- an
adapter never constructed, a port never injected, a module with no callers, a
fallback beside an injected dependency. Every one was found by accident while
chasing something else, which is not a method.

`scripts/check_dead_symbols.py` asks the question deliberately: which public
symbols in `src/` does nothing reference? The answer is baselined in
`pyproject.toml` so the number can only go down, the same ratchet the complexity
baseline and the import-contract ledger use.

Two counts, kept apart on purpose. A symbol nothing references and nothing
exports can be deleted; one that is in an `__all__` is public API, so deleting
it is a release decision. Merging them would hide that difference behind a
single number.
"""

from __future__ import annotations

import pathlib
import sys
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

sys.path.insert(0, str(ROOT / "scripts"))
from check_dead_symbols import scan  # noqa: E402


@pytest.fixture(scope="module")
def baseline() -> tuple[set[str], set[str]]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    section = data["tool"]["dead_symbols"]
    return set(section["unreferenced"]), set(section["exported_unreferenced"])


@pytest.fixture(scope="module")
def current() -> tuple[set[str], set[str]]:
    dead, exported = scan()
    return set(dead), set(exported)


class TestTheBaselineCannotGrow:
    def test_no_new_unreferenced_symbol(self, baseline, current):
        added = sorted(current[0] - baseline[0])
        assert added == [], (
            f"{len(added)} public symbol(s) that nothing references: {added}. "
            "Either the wiring is missing -- which is what this gate exists to catch -- "
            "or the symbol should not have been added. If it is deliberately public for "
            "embedders, put it in an `__all__` and re-run with --update."
        )

    def test_no_new_exported_unreferenced_symbol(self, baseline, current):
        added = sorted(current[1] - baseline[1])
        assert added == [], f"new exported-but-unreferenced symbol(s): {added}"


class TestTheBaselineIsCurrent:
    """A stale entry means something was deleted and the ratchet was not tightened."""

    def test_every_baselined_symbol_still_exists(self, baseline, current):
        stale = sorted((baseline[0] - current[0]) | (baseline[1] - current[1]))
        assert stale == [], (
            f"{len(stale)} baselined symbol(s) no longer match the scan: {stale[:8]}. "
            "Run `python scripts/check_dead_symbols.py --update` to lock the progress in."
        )


class TestTheScannerItself:
    """A scanner with a blind spot produces a baseline that means nothing.

    Each case below is a false positive the first version actually produced,
    kept as a test because the fix is not obvious from reading the scanner.
    """

    def test_an_aliased_import_counts_as_a_use(self, tmp_path):
        """`from x import y as z` uses y. Missing this made a live helper look dead."""
        from check_dead_symbols import _referenced_names
        import ast

        tree = ast.parse("from a.b import used_name as _local\n")
        assert "used_name" in _referenced_names({tmp_path / "x.py": tree})

    def test_a_symbol_used_only_in_its_own_file_is_not_dead(self, current):
        """`get_current_version` is called three times by its own module."""
        assert "infrastructure/persistence/event_serializer.py::get_current_version" not in current[0] | current[1]

    def test_a_route_table_entry_counts_as_a_use(self, current):
        """Handlers are named in a `Route(...)` table, not registered by decorator."""
        assert "server/api/groups.py::list_groups" not in current[0] | current[1]

    def test_a_framework_registered_command_is_not_dead(self, current):
        """`@app.command("zsh")` hands the function to Typer; nothing names it again."""
        assert "server/cli/commands/completion.py::completion_zsh" not in current[0] | current[1]

    def test_an_all_entry_is_not_itself_a_use(self, current):
        """Otherwise exporting something would mark it used and the gate would see nothing."""
        assert current[1], "the exported-but-unreferenced list is empty; __all__ is being counted as a use"
