"""The invariants the fuzz targets assert, importable without atheris.

Separated from the harnesses on purpose. `atheris` publishes manylinux x86_64
wheels for CPython 3.12-3.14 only, so it cannot be installed on this project's
3.11 baseline or on a developer's macOS machine (see `README.md` here). If the
invariants lived inside the harnesses, nothing outside the fuzzing job could
run them -- and the invariants are the interesting part, not the mutation loop.

So: this module holds the checks, the `fuzz_*.py` wrappers feed them bytes
under atheris, and `tests/unit/test_the_fuzz_invariants_hold_on_the_corpus.py`
replays the corpus through the same functions on every PR, everywhere.

An invariant raises `InvariantViolated` when it is broken. A crash inside the
code under test propagates as itself -- that is the finding, and libFuzzer
records it either way.
"""

from __future__ import annotations

import time
from typing import Any

#: A single evaluation is a decision on one tool call; it is not allowed to
#: take this long. The budget exists because `scan_arguments` runs
#: agent-controlled payload through ten regex groups (`SECRET_PATTERN_GROUPS`),
#: which is where catastrophic backtracking would show up. A wall-clock budget
#: is coarse, and deliberately so: what matters is the difference between
#: microseconds and seconds, not a precise figure.
EVALUATE_BUDGET_SECONDS = 2.0


class InvariantViolated(AssertionError):
    """A property that must hold of every input did not hold of this one."""


def check_evaluate(tool_name: str, arguments: Any, policy_data: dict[str, Any], headers: dict[str, str]) -> None:
    """`evaluate()` answers with a Decision, in bounded time, for any input.

    Not "the answer is X" -- the answer is the policy's business. The property
    is that there *is* an answer: a call that cannot be judged must still end
    in a verdict the caller can act on, or it ends as an unattributed failure
    in every mode (which is what #1102 was).
    """
    from mcp_hangar.domain.policies.egress_l7 import Decision, L7Policy, evaluate

    try:
        policy = L7Policy.from_dict(policy_data)
    except ValueError:
        return  # A rejected policy is a declared outcome; nothing to evaluate.

    started = time.monotonic()
    decision = evaluate(tool_name, arguments, policy, headers)
    elapsed = time.monotonic() - started

    if not isinstance(decision, Decision):
        raise InvariantViolated(f"evaluate returned {type(decision).__name__}, not a Decision")
    if elapsed > EVALUATE_BUDGET_SECONDS:
        raise InvariantViolated(f"evaluate took {elapsed:.1f}s, over the {EVALUATE_BUDGET_SECONDS}s budget")


def check_policy_parse(data: Any) -> None:
    """A policy parser either returns an object or raises its declared error.

    Both parsers document `ValueError` for anything malformed. A `KeyError`, an
    `AttributeError`, a `TypeError` or a `RecursionError` is a parser reached by
    input it did not expect -- and both of these read operator-authored CRD
    payloads, so "did not expect" means an operator can crash the gateway's
    configuration path with a typo.
    """
    from mcp_hangar.domain.policies.dsl import parse_policy
    from mcp_hangar.domain.policies.egress_l7 import L7Policy

    for parser in (L7Policy.from_dict, parse_policy):
        try:
            parser(data)
        except ValueError:
            pass  # Declared.
        except Exception as exc:  # noqa: BLE001 -- undeclared is the finding
            raise InvariantViolated(f"{parser.__qualname__} raised {type(exc).__name__}: {exc}") from exc


def check_access_precedence(
    tool_name: str,
    allow: list[str],
    deny: list[str],
    approval: list[str],
    other_allow: list[str],
    other_deny: list[str],
) -> None:
    """Deny wins over every other list, and keeps winning after a merge.

    The only invariant here whose breach is a policy BYPASS rather than a
    crash. `_matches_any_pattern` rests on `fnmatch`, and the
    deny > approval > allow > default order is recomputed by `merge()` and by
    the composite policy it returns -- three places that have to agree, which
    is the shape of a rule that eventually does not.
    """
    from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy

    try:
        policy = ToolAccessPolicy(allow_list=allow, deny_list=deny, approval_list=approval)
        other = ToolAccessPolicy(allow_list=other_allow, deny_list=other_deny)
    except ValueError:
        # `__post_init__` rejects an empty or non-string pattern. That is a
        # declared outcome, the same standing `check_evaluate` gives a policy
        # `from_dict` refuses -- there is no policy here to have precedence.
        # The fuzzer found this on its second input, as a crash in this file
        # rather than in the code under test.
        return

    denied_by_either = _matches(policy, tool_name) or _matches(other, tool_name)

    if _matches(policy, tool_name) and policy.is_tool_allowed(tool_name):
        raise InvariantViolated(f"{tool_name!r} matches deny_list {deny!r} and was allowed")

    for merged, label in (
        (ToolAccessPolicy.merge(policy, other), "merge(policy, other)"),
        (ToolAccessPolicy.merge(other, policy), "merge(other, policy)"),
    ):
        if denied_by_either and merged.is_tool_allowed(tool_name):
            raise InvariantViolated(f"{tool_name!r} is denied by one side but allowed after {label}")


def _matches(policy: Any, tool_name: str) -> bool:
    """Whether *tool_name* matches this policy's deny list, by the policy's own matcher."""
    from fnmatch import fnmatchcase

    return any(fnmatchcase(tool_name, pattern) for pattern in policy.deny_list or ())
