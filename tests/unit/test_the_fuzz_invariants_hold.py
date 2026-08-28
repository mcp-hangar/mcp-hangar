"""The fuzz invariants, run on every PR without a fuzzer.

`atheris` publishes manylinux x86_64 wheels for CPython 3.12-3.14 only, so it
installs on neither this project's 3.11 baseline nor a macOS machine. If the
invariants lived inside the harnesses they would run in exactly one place, and
the invariants are the interesting part -- the mutation loop only searches for
inputs that break them.

So `fuzz/invariants.py` holds the checks, the `fuzz/fuzz_*.py` wrappers feed
them fuzzer bytes, and this file replays the seed corpus plus the findings that
have already been made through the same functions. A regression that a fuzzer
found once is then checked forever, on every platform, in 40 milliseconds.
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "fuzz"))

from invariants import (  # noqa: E402 -- the path insert above has to come first
    check_access_precedence,
    check_evaluate,
    check_policy_parse,
)

PARSE_CORPUS = sorted((ROOT / "fuzz" / "corpus" / "parse").glob("*.json"))


def _nested(depth: int) -> dict[str, Any]:
    root: dict[str, Any] = {}
    cursor = root
    for _ in range(depth):
        cursor["a"] = {}
        cursor = cursor["a"]
    return root


_ENFORCE = {
    "mode": "enforce",
    "tools": {"allow": ["some_tool"], "deny": ["drop_*"]},
    "defaultAction": "Deny",
    "arguments": {"secretPatterns": ["aws-keys"], "maxPayloadBytes": 100_000},
}
_MODERN = {"mcp-protocol-version": "2026-07-28"}


class TestTheCorpusIsThere:
    def test_the_parse_corpus_is_not_empty(self) -> None:
        """Guards the glob: an empty corpus makes the replay below vacuous."""
        assert len(PARSE_CORPUS) >= 5


class TestEvaluateAlwaysAnswers:
    @pytest.mark.parametrize("depth", [0, 10, 1_000, 100_000])
    def test_nesting_of_any_depth_gets_a_verdict(self, depth: int) -> None:
        """The #1102 regression: 992 levels used to raise `RecursionError`."""
        check_evaluate("some_tool", _nested(depth), _ENFORCE, _MODERN)

    def test_audit_mode_answers_too(self) -> None:
        """Audit aborting the call is the opposite of ADR-013's adoption path."""
        check_evaluate("some_tool", _nested(1_200), {**_ENFORCE, "mode": "audit"}, _MODERN)

    @pytest.mark.parametrize(
        "arguments",
        [
            {"path": "/data/notes.txt"},
            {"body": "AKIAIOSFODNN7EXAMPLE"},
            "a bare string, which is used as-is",
            {"lone-surrogate": "\ud800"},
            [{"deep": _nested(50)}, None, 1.5],
            None,
        ],
        ids=["plain", "secret", "bare-string", "lone-surrogate", "mixed", "none"],
    )
    def test_ordinary_and_awkward_arguments_get_a_verdict(self, arguments: Any) -> None:
        check_evaluate("some_tool", arguments, _ENFORCE, _MODERN)


class TestPolicyParsersRejectRatherThanCrash:
    @pytest.mark.parametrize("seed", PARSE_CORPUS, ids=lambda p: p.name)
    def test_the_corpus_parses_or_is_declared_invalid(self, seed: pathlib.Path) -> None:
        import json

        try:
            decoded = json.loads(seed.read_text())
        except RecursionError:
            pytest.skip("the JSON decoder itself cannot read this seed; that is not our surface")
        check_policy_parse(decoded)


class TestDenyKeepsWinning:
    @pytest.mark.parametrize(
        ("tool", "allow", "deny", "other_allow", "other_deny"),
        [
            ("drop_db", ["*"], ["drop_*"], ["*"], []),
            ("drop_db", ["drop_db"], ["drop_db"], [], []),
            ("read_a", ["read_*"], [], [], ["read_?"]),
            ("a.b", ["*"], ["a.b"], ["a.b"], []),
            ("x", ["[x]"], ["[x]"], [], []),
        ],
        ids=["glob-vs-star", "exact-both", "denied-by-other", "dotted", "bracket"],
    )
    def test_a_denied_tool_is_never_allowed(
        self, tool: str, allow: list[str], deny: list[str], other_allow: list[str], other_deny: list[str]
    ) -> None:
        check_access_precedence(tool, allow, deny, [], other_allow, other_deny)
