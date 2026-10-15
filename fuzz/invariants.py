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
from collections.abc import Callable
from fnmatch import fnmatchcase
from typing import Any

# Imported at module level, NOT lazily inside each check. The harnesses import
# this module inside `atheris.instrument_imports()`, and instrumentation
# applies to what is imported while that context is open -- so a lazy import
# inside a function runs long after it has closed, and the code under test is
# loaded uninstrumented.
#
# That was not a subtle degradation: 1.6 million inputs at a constant
# `cov: 4 ft: 4`, a corpus that never grew past its one empty seed, and a
# green job the whole time (#1112). A blind fuzzer still finds shallow bugs,
# which is exactly why the green looked like evidence.
from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.policies.dsl import parse_policy
from mcp_hangar.domain.policies.egress_l7 import Decision, L7Policy, evaluate
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.fastmcp_server.flat_tool_projection import generate_projection, is_governed_allowed
from mcp_hangar.server.config import build_config
from mcp_hangar.server.config_schema import validate_config

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
    return any(fnmatchcase(tool_name, pattern) for pattern in policy.deny_list or ())


# --- the front door and the configuration (#1787) ---------------------------------------

#: A chooser: `choose(n)` is an int in `range(n)`. The fuzz harnesses back it with
#: `FuzzedDataProvider`, the replay test with a seeded `random.Random`, so the same
#: structure-aware generator runs under both.
Choose = Callable[[int], int]

_SERVERS = ("s0", "s1", "s2")
#: Names with `fnmatch` metacharacters among them: where a policy and a name disagree.
_TOOLS = ("t0", "t1", "read_file", "a*b", "x?", "[z]")
_TENANTS: tuple[str | None, ...] = (None, "t1", "t2")
_PATTERNS = ("*", "t*", "read_*", "a*b", "x?", "t0", "[z]")


def _pick(choose: Choose, options: tuple[Any, ...]) -> Any:
    return options[choose(len(options))]


def _some(choose: Choose, options: tuple[Any, ...], limit: int = 3) -> list[Any]:
    return [_pick(choose, options) for _ in range(choose(limit + 1))]


def check_front_door_projection(choose: Choose) -> int:
    """What the front door lists a caller is allowed and not withdrawn, and listed means routed.

    A random fleet (servers, tools with metacharacters in their names), random
    withdrawals and random access policies, then `generate_projection` for each
    caller. The property is the one every path-divergence defect broke: a name
    in the listing is a name the governed checks pass for that caller, so it can
    be called, and the listing and the routing it dispatches on agree. The
    converse is not asserted -- a name can be absent for reasons that are not
    policy (a collision, a header rule) -- but a name shown that policy denies,
    or that is withdrawn, is a bypass.

    Returns:
        How many names were listed in all, so a replay can assert it looked at
        something.
    """
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    try:
        resolver = get_tool_access_resolver()
        resolver.set_topology_mode("front_door")
        registry = get_tool_projection_registry()

        for server in _some(choose, _SERVERS, 3):
            names = sorted(set(_some(choose, _TOOLS, 4)))
            registry.build_from_tools(server, [ToolSchema(name=n, description="", input_schema={}) for n in names])
            for _ in range(choose(3)):
                registry.set_config_withdrawal(server, _pick(choose, _TOOLS), _pick(choose, _TENANTS))
            for _ in range(choose(3)):
                try:
                    policy = ToolAccessPolicy(allow_list=_some(choose, _PATTERNS), deny_list=_some(choose, _PATTERNS))
                except ValueError:
                    continue
                tenant = _pick(choose, _TENANTS)
                if tenant is None:
                    resolver.set_mcp_server_policy(server, policy)
                else:
                    resolver.set_standalone_member_policy(server, tenant, policy)

        listed = 0
        for tenant in _TENANTS:
            projection = generate_projection(tenant)
            listed += len(projection.routes)
            if set(projection.routes) != set(projection.tools):
                raise InvariantViolated(
                    f"tenant {tenant!r}: routes {sorted(projection.routes)} differ from the listing "
                    f"{sorted(projection.tools)}"
                )
            for flat_name, (server, tool) in projection.routes.items():
                if not is_governed_allowed(server, tool, kind="tool", tenant_id=tenant):
                    raise InvariantViolated(f"tenant {tenant!r}: {server}/{tool} is listed as {flat_name!r} but denied")
                if registry.is_withdrawn(server, tool, tenant_id=tenant):
                    raise InvariantViolated(f"tenant {tenant!r}: {server}/{tool} is listed but withdrawn")
        return listed
    finally:
        reset_tool_projection_registry()
        reset_tool_access_resolver()


_SCALARS: tuple[Any, ...] = (None, True, False, 0, 1, -1, 2.5, "", "x", "add", [], {}, [1], ["a"], {"a": 1})
_SPEC_KEYS = (
    "mode",
    "command",
    "args",
    "image",
    "endpoint",
    "env",
    "tools",
    "access",
    "tool_access",
    "tool_projection",
    "capabilities",
    "health_check_interval_s",
    "max_consecutive_failures",
    "idle_ttl_s",
    "http",
    "members",
    "user",
    "volumes",
    "x",
)
_NESTED_KEYS = (
    "allow_list",
    "deny_list",
    "approval_list",
    "pins",
    "withdrawn",
    "tenant_overrides",
    "upstream",
    "new_tools",
    "member",
    "prompt",
    "resource",
    "network",
    "x",
)


def _value(choose: Choose, depth: int = 0) -> Any:
    kind = choose(8)
    if depth >= 3 or kind < 4:
        return _pick(choose, _SCALARS)
    if kind < 6:
        return [_value(choose, depth + 1) for _ in range(choose(4))]
    return {_pick(choose, _NESTED_KEYS): _value(choose, depth + 1) for _ in range(choose(4))}


def build_config_document(choose: Choose) -> dict[str, Any]:
    """A configuration document built from the schema's own keys, with values of the right and the wrong shape.

    Structure-aware where a byte-level mutator is not: the keys are the ones the
    loader reads, the modes are real, and the values are drawn from scalars,
    lists and mappings at each level, so a block of the wrong kind reaches the
    reader that expected another.
    """
    servers: dict[str, Any] = {}
    for index in range(choose(3) + 1):
        spec: dict[str, Any] = {}
        mode = _pick(choose, ("subprocess", "docker", "remote", "group", "nosuch", None, 1))
        if mode is not None:
            spec["mode"] = mode
        for _ in range(choose(6)):
            spec[_pick(choose, _SPEC_KEYS)] = _value(choose)
        servers[f"s{index}"] = spec
    return {"mcp_servers": servers}


def check_config_document(choose: Choose) -> None:
    """A configuration is checked or refused by name, never by a crash.

    `validate_config` reports unknown keys and must answer for any document. The
    loader (`build_config`, which puts nothing in force) either builds it or
    refuses it with `ConfigurationError`, which names the server. Anything else
    -- an `AttributeError` from a `.get` on a string, a `TypeError`, a bare
    `ValueError` -- is an operator's typo ending boot in a traceback that names
    nothing.
    """
    document = build_config_document(choose)
    try:
        problems = validate_config(document)
    except Exception as exc:  # noqa: BLE001 -- any exception is the finding
        raise InvariantViolated(f"validate_config raised {type(exc).__name__}: {exc}") from exc
    if not isinstance(problems, list) or not all(isinstance(p, str) for p in problems):
        raise InvariantViolated(f"validate_config returned {problems!r}")

    try:
        build_config(document["mcp_servers"])
    except ConfigurationError:
        pass  # Declared.
    except Exception as exc:  # noqa: BLE001 -- undeclared is the finding
        raise InvariantViolated(f"build_config raised {type(exc).__name__}: {exc}") from exc
