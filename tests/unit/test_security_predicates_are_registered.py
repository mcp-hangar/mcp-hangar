"""A function shaped like a security predicate is registered, or exempt with a reason (#1386).

The reachability test can only hold a predicate to its paths once the predicate
is in its table. This check keeps the table from going stale. It flags every
function in ``src/`` whose name matches ``PREDICATE_NAME_PATTERNS``, whether it
is a module function, a method or a nested function. Each one must be in
``PREDICATES`` or in ``EXEMPT``, and an exemption must say why. It is the same
ratchet as the dead-symbol and complexity baselines, with a reason on every
entry, because an allowlist without reasons cannot be reviewed.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

from tests.unit._security_predicates import EXEMPT, PREDICATE_NAME_PATTERNS, PREDICATES, SRC

_PATTERNS = [re.compile(pattern) for pattern in PREDICATE_NAME_PATTERNS]


def _defs(root: Path, *, shaped_only: bool) -> set[str]:
    """Every function under *root*, as ``<relative path>::<qualname>``.

    The qualname is built the way Python builds ``__qualname__``, including
    ``<locals>`` for a function defined inside another, so a registered
    function's ``Predicate.location`` and its scanned key are the same string.
    """
    found: set[str] = set()
    for source in sorted(root.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for qualname, name in _qualnames(tree, ""):
            if not shaped_only or any(p.search(name) for p in _PATTERNS):
                found.add(f"{source.relative_to(root).as_posix()}::{qualname}")
    return found


def _qualnames(node: ast.AST, prefix: str) -> list[tuple[str, str]]:
    """``(qualname, name)`` for every function under *node*, nested ones included."""
    out: list[tuple[str, str]] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            qualname = f"{prefix}{child.name}"
            out.append((qualname, child.name))
            out += _qualnames(child, f"{qualname}.<locals>.")
        elif isinstance(child, ast.ClassDef):
            out += _qualnames(child, f"{prefix}{child.name}.")
        else:
            out += _qualnames(child, prefix)
    return out


@pytest.fixture(scope="module")
def shaped() -> set[str]:
    return _defs(SRC, shaped_only=True)


_REGISTERED = {predicate.location for predicate in PREDICATES}


class TestEveryPredicateShapedFunctionIsAccountedFor:
    def test_it_is_registered_or_exempt(self, shaped: set[str]) -> None:
        unaccounted = sorted(shaped - _REGISTERED - set(EXEMPT))

        assert not unaccounted, (
            f"{len(unaccounted)} function(s) named like a security predicate are neither registered nor exempt: "
            f"{unaccounted}. If it gates a request, add it to PREDICATES in tests/unit/_security_predicates.py "
            "with the served paths it must run on. If it does not, add it to EXEMPT with the reason."
        )

    def test_no_exemption_is_stale(self, shaped: set[str]) -> None:
        stale = sorted(set(EXEMPT) - shaped)

        assert not stale, f"EXEMPT names functions that are gone or no longer predicate-shaped: {stale}"

    def test_nothing_is_both_registered_and_exempt(self) -> None:
        both = sorted(_REGISTERED & set(EXEMPT))

        assert not both, f"registered and exempt at once: {both}"

    def test_every_exemption_gives_a_one_line_reason(self) -> None:
        unexplained = sorted(key for key, reason in EXEMPT.items() if not reason.strip() or "\n" in reason)

        assert not unexplained, f"exemptions without a one-line reason: {unexplained}"

    def test_every_registered_predicate_is_found_where_the_table_says(self) -> None:
        # Guards the key itself: a location the scan cannot produce would let a
        # registered predicate be flagged as unaccounted, or hide a typo.
        missing = sorted(_REGISTERED - _defs(SRC, shaped_only=False))

        assert not missing, f"registered locations the scan cannot find: {missing}"


class TestTheScannerItself:
    """A scanner with a blind spot makes an allowlist that means nothing."""

    def test_a_new_predicate_shaped_function_is_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "scratch.py").write_text(
            "def is_scratch_allowed(): ...\n"
            "def refuse_if_scratch_blocked(): ...\n"
            "class Scratch:\n"
            "    def authorize(self): ...\n"
            "    async def _check_scratch_permission(self): ...\n"
            "def outer():\n"
            "    def _enforce_scratch(): ...\n"
        )

        assert _defs(tmp_path, shaped_only=True) == {
            "scratch.py::is_scratch_allowed",
            "scratch.py::refuse_if_scratch_blocked",
            "scratch.py::Scratch.authorize",
            "scratch.py::Scratch._check_scratch_permission",
            "scratch.py::outer.<locals>._enforce_scratch",
        }

    def test_a_scratch_predicate_in_the_tree_would_fail_the_check(self, tmp_path: Path) -> None:
        (tmp_path / "scratch.py").write_text("def is_scratch_allowed(): ...\n")

        flagged = _defs(tmp_path, shaped_only=True)

        assert flagged - _REGISTERED - set(EXEMPT) == {"scratch.py::is_scratch_allowed"}

    @pytest.mark.parametrize(
        "name",
        [
            "is_ready",
            "allowed_hosts",
            "refuse_local_modes_in_a_declared_cluster",
            "check_health",
            "enforcement_mode",
            "can_start",
        ],
    )
    def test_a_name_that_only_looks_close_is_not_flagged(self, tmp_path: Path, name: str) -> None:
        (tmp_path / "near.py").write_text(f"def {name}(): ...\n")

        assert _defs(tmp_path, shaped_only=True) == set()
