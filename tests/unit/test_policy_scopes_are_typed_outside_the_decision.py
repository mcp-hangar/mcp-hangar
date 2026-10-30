"""No policy-scope argument is built from loose strings outside the decision (#1784).

Every scope-key defect went the same way: a surface assembled ``(server id,
group id, member id)`` by hand, and nothing stopped a server id landing in the
tenant slot (#1763). ``decide(target, name, caller)`` closed that, and two
things keep it closed. ``.importlinter`` forbids importing ``_policy_merge``; an
import contract cannot see the shape of a call, so this test does.

Maintainer decision 8 on #1763 sets the reach: the rule is on POLICY-SCOPE
arguments. A ``tenant_id`` handed to the withdrawal registry, audit or quotas is
legitimate, and still has to be a tenant. So, over ``src/`` outside
``DECISION_MODULES``:

* the resolver is used through its registration API and nothing else;
* no call passes the merge's scope arguments, under any receiver;
* a scope reaches the decision through ``Target.server``/``Target.group`` and
  ``Caller`` only, and no slot of those is a built string;
* a tenant slot holds a name that says tenant, and a server slot does not.

The last is a check on names, so it cannot prove a value. The runtime half is
``test_security_predicates_are_reachable.py``, whose mutation puts a server id in
the tenant slot; this half fails on the source, before anything runs.

Tests are outside the scan: the merge's own tests must speak its vocabulary.
``FILES_THAT_TEST_THE_MERGE`` names them, so a surface test cannot join quietly.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest

import mcp_hangar
from mcp_hangar.domain.services.tool_access_resolver import ToolAccessResolver

SRC = Path(mcp_hangar.__file__).resolve().parent
TESTS = Path(__file__).resolve().parents[1]

#: Where a policy scope is legitimately spelled out.
DECISION_MODULES = frozenset(
    {
        "domain/services/_policy_merge.py",
        "domain/services/access_decision.py",
        "domain/services/tool_access_resolver.py",
        "domain/value_objects/access.py",
    }
)

#: All a holder of the resolver may touch. Nothing here reads a decision.
REGISTRATION_API = frozenset(
    {
        "adopt_config_policies",
        "clear_all",
        "get_configured_policy",
        "get_policy_summary",
        "invalidate_cache",
        "iter_registered_policies",
        "locked",
        "remove_group_policy",
        "remove_mcp_server_policy",
        "remove_member_policy",
        "remove_provider_policy",
        "reset",
        "set_group_policy",
        "set_mcp_server_policy",
        "set_member_policy",
        "set_provider_policy",
        "set_standalone_member_policy",
        "set_topology_mode",
        "topology_mode",
    }
)

#: Tests of the merge itself, the oracle `decide()` is held to, and fakes that stand in for it.
FILES_THAT_TEST_THE_MERGE = frozenset(
    {
        "benchmark/test_proxy_overhead.py",
        "integration/test_tool_filtering.py",
        "unit/_predicate_reachability_harness.py",
        "unit/_security_predicates.py",
        "unit/domain/services/test_tool_access_resolver.py",
        "unit/test_a_group_call_asks_a_human_for_its_members_l7_rule.py",
        "unit/test_a_group_members_own_policy_reaches_the_verdict.py",
        "unit/test_a_reload_swaps_the_governance_overlays_as_one_set.py",
        "unit/test_a_tool_call_reads_a_reload_as_one_set.py",
        "unit/test_a_task_follow_up_is_checked_against_current_tool_access.py",
        "unit/test_approval_gate_reachability.py",
        "unit/test_batch_revalidate_after_hold.py",
        "unit/test_config_policies_are_adopted_in_one_step.py",
        "unit/test_decide_agrees_with_the_executor_on_every_target_shape.py",
        "unit/test_front_door_mode.py",
        "unit/test_member_scope_policy.py",
        "unit/test_prompts_and_resources_are_governed_by_policy.py",
        "unit/test_security_predicates_are_reachable.py",
    }
)

#: The names of the retired read API: never spelled outside the decision.
RETIRED = frozenset({"resolve_effective_policy", "_compute_effective_policy", "filter_tool_dicts"})
#: Also the names of a value object's one-argument match, so these go by shape.
RETIRED_BY_SHAPE = frozenset({"is_allowed", "is_tool_allowed", "filter_tools"})
SCOPE_KEYWORDS = frozenset({"group_id", "member_id", "member_server_id"})

_SERVER_LIKE = re.compile(r"server|member|group|provider|upstream|selected|addressed|routed")
_IMPORTS_THE_MERGE = re.compile(r"import _policy_merge|\._policy_merge import|^\s+_policy_merge,$", re.MULTILINE)
_RESOLVER_NAMES = frozenset({"resolver", "policies"})
_RESOLVER_SOURCES = frozenset({"get_tool_access_resolver", "_new_policies"})


def _identifiers(node: ast.AST) -> set[str]:
    """The variable and attribute names an expression is made of."""
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
    return found


def _is_built_string(node: ast.AST) -> bool:
    """A literal, an f-string, a concatenation or a `.format`/`.join`: a scope made by hand."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr | ast.BinOp):
        return True
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("format", "join")


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_resolver(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in _RESOLVER_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr == "policies"
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _RESOLVER_SOURCES


def _call_name(node: ast.Call) -> str:
    return node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")


def _aliases(tree: ast.Module) -> dict[str, str]:
    """`import Caller as AccessCaller` is how the executor spells it: a rule must see through it."""
    return {
        alias.asname: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname is not None and alias.name in ("Caller", "Target", "decide")
    }


def _is_target_factory(node: ast.Call, aliases: dict[str, str]) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in ("server", "group")
        and isinstance(func.value, ast.Name)
        and aliases.get(func.value.id, func.value.id) == "Target"
    )


def _argument(node: ast.Call, index: int, keyword: str) -> ast.expr | None:
    for candidate in node.keywords:
        if candidate.arg == keyword:
            return candidate.value
    return node.args[index] if index < len(node.args) else None


def _tenant_slot(value: ast.AST | None, where: str) -> Iterator[str]:
    """A tenant slot of the decision: a name that says tenant, or None. Nothing else."""
    if value is None or _is_none(value):
        return
    names = _identifiers(value)
    if _is_built_string(value) or not any("tenant" in name for name in names):
        yield f"{where} does not hold a tenant"
    yield from _no_server_in_tenant_slot(names, where)


def _no_server_in_tenant_slot(names: set[str], where: str) -> Iterator[str]:
    for name in sorted(names):
        if _SERVER_LIKE.search(name) and "tenant" not in name:
            yield f"{where} holds {name!r}, a server id in the tenant slot"


def _server_slot(value: ast.AST | None, where: str) -> Iterator[str]:
    if value is None or _is_none(value):
        return
    if _is_built_string(value):
        yield f"{where} is a built string"
    for name in sorted(_identifiers(value)):
        if "tenant" in name:
            yield f"{where} holds {name!r}, a tenant in a server slot"


def _tenant_positions(trees: dict[str, ast.Module]) -> dict[str, int]:
    """Where each function in `src/` takes `tenant_id`, for names with one answer."""
    seen: dict[str, set[int]] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                params = [a.arg for a in (*node.args.posonlyargs, *node.args.args) if a.arg not in ("self", "cls")]
                position = params.index("tenant_id") if "tenant_id" in params else -1
                seen.setdefault(node.name, set()).add(position)
    return {name: next(iter(found)) for name, found in seen.items() if len(found) == 1 and -1 not in found}


def _call_violations(node: ast.Call, tenant_positions: dict[str, int], aliases: dict[str, str]) -> Iterator[str]:
    name = aliases.get(_call_name(node), _call_name(node))
    if name in RETIRED_BY_SHAPE and (len(node.args) > 1 or SCOPE_KEYWORDS & {k.arg for k in node.keywords}):
        yield f"{name}() takes a policy scope outside the decision"
    if name == "Target":
        yield "Target() built directly: use Target.server or Target.group"
    if name == "Caller":
        yield from _tenant_slot(_argument(node, 1, "tenant_id"), "Caller.tenant_id")
    if _is_target_factory(node, aliases):
        yield from _server_slot(node.args[0] if node.args else None, f"Target.{name} addressed")
        for keyword in node.keywords:
            if keyword.arg in ("member_server", "owning_groups"):
                yield from _server_slot(keyword.value, f"Target.{keyword.arg}")
    if name == "decide":
        target, caller = _argument(node, 0, "target"), _argument(node, 2, "caller")
        if target is None or _is_built_string(target):
            yield "decide() was not handed a Target"
        if caller is None or _is_built_string(caller) or not any("caller" in n.lower() for n in _identifiers(caller)):
            yield "decide() was not handed a Caller"
    tenant = _argument(node, tenant_positions.get(name, len(node.args)), "tenant_id")
    if tenant is not None and not _is_none(tenant):
        yield from _no_server_in_tenant_slot(_identifiers(tenant), f"tenant_id of {name}()")


def violations(tree: ast.Module, tenant_positions: dict[str, int] | None = None) -> list[str]:
    """Every way *tree* spells a policy scope outside the decision, with its line."""
    positions = tenant_positions or {}
    aliases = _aliases(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        problems: list[str] = []
        if isinstance(node, ast.Attribute):
            if node.attr in RETIRED:
                problems.append(f"{node.attr} is the retired read API")
            if node.attr == "policy_scopes":
                problems.append("policy_scopes() is the decision's")
            if _is_resolver(node.value) and node.attr not in REGISTRATION_API:
                problems.append(f"resolver.{node.attr} is not registration API")
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and "policy_scopes" in node.name:
            problems.append(f"{node.name}() assembles policy scopes outside the decision")
        elif isinstance(node, ast.Call):
            problems.extend(_call_violations(node, positions, aliases))
        found.extend(f"line {getattr(node, 'lineno', '?')}: {problem}" for problem in problems)
    return found


@pytest.fixture(scope="module")
def trees() -> dict[str, ast.Module]:
    return {
        path.relative_to(SRC).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in SRC.rglob("*.py")
        if path.relative_to(SRC).as_posix() not in DECISION_MODULES
    }


class TestTheSourceTree:
    def test_no_policy_scope_is_spelled_outside_the_decision(self, trees: dict[str, ast.Module]) -> None:
        positions = _tenant_positions(trees)
        found = {name: problems for name, tree in trees.items() if (problems := violations(tree, positions))}
        assert not found

    def test_the_scan_saw_the_decision_being_asked(self, trees: dict[str, ast.Module]) -> None:
        """Zero findings from a scan that matched nothing would prove nothing."""
        calls: Counter[str] = Counter()
        factories = 0
        for tree in trees.values():
            aliases = _aliases(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    calls[aliases.get(_call_name(node), _call_name(node))] += 1
                    factories += _is_target_factory(node, aliases)
        assert calls["decide"] >= 3
        assert calls["Caller"] >= 3
        assert factories >= 3
        assert _tenant_positions(trees)["_targets_of_a_call"] == 1

    def test_the_decision_feeds_the_merge_the_callers_tenant(self) -> None:
        tree = ast.parse((SRC / "domain/services/access_decision.py").read_text(encoding="utf-8"))
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "_policy_merge"
        ]
        assert {_call_name(n) for n in calls} == {"is_allowed", "resolve_effective_policy"}
        for call in calls:
            tenant = _argument(call, 3, "member_id")
            assert isinstance(tenant, ast.Name) and tenant.id == "tenant_id", ast.unparse(call)
        tenants = [
            ast.unparse(n.value)
            for n in ast.walk(tree)
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "tenant_id" for t in n.targets)
        ]
        assert tenants == ["caller.tenant_id"]


class TestTheResolverSurface:
    def test_the_resolver_offers_registration_and_no_read(self) -> None:
        """A read added back as a method is reachable from anything holding the resolver."""
        public = {name for name in vars(ToolAccessResolver) if not name.startswith("_")}
        assert public == REGISTRATION_API

    def test_the_merge_is_imported_only_by_the_tests_that_test_it(self) -> None:
        importing = {
            path.relative_to(TESTS).as_posix()
            for path in TESTS.rglob("*.py")
            if path != Path(__file__).resolve() and _IMPORTS_THE_MERGE.search(path.read_text(encoding="utf-8"))
        }
        assert importing == FILES_THAT_TEST_THE_MERGE


#: Each is a defect this test exists to refuse, and the finding it must raise.
SEEDED = [
    pytest.param("resolver.resolve_effective_policy('s', 'g', 't')", "retired read API", id="the-old-read"),
    pytest.param("get_tool_access_resolver().is_tool_allowed('s', 't')", "not registration API", id="singleton"),
    pytest.param("resolver.anything_new(server, group, tenant)", "not registration API", id="a-new-read"),
    pytest.param("thing.is_allowed(server, tool, member_id=tenant)", "takes a policy scope", id="any-receiver"),
    pytest.param("target.policy_scopes()", "the decision's", id="scopes-read"),
    pytest.param("def _policy_scopes(s, g): ...", "assembles policy scopes", id="scopes-assembled"),
    pytest.param("Target('s', 'server')", "built directly", id="target-direct"),
    pytest.param("Caller(tenant_id=member_server)", "a server id in the tenant slot", id="the-epic-defect"),
    pytest.param("Caller(None, selected)", "a server id in the tenant slot", id="positional-tenant"),
    pytest.param(
        "from x import Caller as AccessCaller\nAccessCaller(tenant_id=member_server)",
        "a server id in the tenant slot",
        id="an-aliased-caller",
    ),
    pytest.param("Caller(tenant_id=f'tenant:{x}')", "does not hold a tenant", id="tenant-built"),
    pytest.param("Caller(tenant_id=x)", "does not hold a tenant", id="tenant-unnamed"),
    pytest.param("Target.group(g, member_server=tenant_id)", "a tenant in a server slot", id="slots-swapped"),
    pytest.param("Target.group(f'group:{g}')", "a built string", id="group-built"),
    pytest.param("Target.server(s, owning_groups=tenant_id)", "a tenant in a server slot", id="owner-is-tenant"),
    pytest.param("decide('server', name, caller)", "not handed a Target", id="decide-string-target"),
    pytest.param("decide(target, name, tenant_id)", "not handed a Caller", id="decide-string-caller"),
    pytest.param("audit.record(tool, tenant_id=mcp_server_id)", "a server id in the tenant slot", id="downstream"),
]


class TestItRefusesWhatItIsFor:
    @pytest.mark.parametrize(("source", "finding"), SEEDED)
    def test_a_seeded_violation_is_found(self, source: str, finding: str) -> None:
        found = violations(ast.parse(source))
        assert any(finding in problem for problem in found), found

    def test_a_server_in_a_positional_tenant_slot_is_found(self) -> None:
        source = "is_governed_allowed(server, name, mcp_server)"
        assert violations(ast.parse(source), {"is_governed_allowed": 2})

    @pytest.mark.parametrize(
        "source",
        [
            "Caller(tenant_id=tenant_id or None)",
            "Caller(tenant_id=None)",
            "Caller()",
            "Target.group(group_id, member_server=member, kind=kind)",
            "Target.server(mcp_server, owning_groups=owning_groups)",
            "decide(target, name, caller, resolver=resolver, withdrawals=registry)",
            "decide(target, name, Caller(tenant_id=tenant_id))",
            "resolver.set_member_policy('g', 'm', policy)",
            "policy.is_tool_allowed(name)",
            "policy.is_allowed(uri)",
            "registry.is_withdrawn(server, name, tenant_id=tenant_id)",
        ],
    )
    def test_the_legitimate_forms_pass(self, source: str) -> None:
        assert violations(ast.parse(source)) == []
