"""`decide()` agrees with the executor's composition on every target shape (#1784).

PR 1 of the plan on #1763 adds the typed `Target`, `Caller` and `Decision` and
the one `decide()` entry point, and migrates no caller. This is the proof that
the callers can be migrated without a change in what they decide. The oracle is
the executor's own composition, taken as it is: `_policy_scopes`,
`_allowed_in_every_scope`, `_resolve_projection`, `_withdrawn_in_scope` and
`_approval_policy`, asked in the order the gates ask them.

For a prompt or a resource, which the executor does not serve, the oracle is
the front door's two halves (`is_governed_allowed`): the overlay with
`is_withdrawn`, the policy with `is_allowed`, under the same scopes.

Every example builds its own resolver and registry and hands them to both
sides, so nothing here touches the process singletons. Projections are built
under server ids only, which is the only way production builds them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from mcp_hangar.application.read_models.tool_projection import ToolProjectionRegistry
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.access_decision import decide
from mcp_hangar.domain.services.tool_access_resolver import ToolAccessResolver
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.domain.value_objects.access import POLICY_KINDS, Caller, Decision, Target
from mcp_hangar.observability.conventions import Gate
from mcp_hangar.server.tools.batch import executor

SERVERS = ("s1", "s2", "s3")
GROUPS = ("g1", "g2")
TENANTS = (None, "tenant:a", "tenant:b")
NAMES = ("alpha", "beta", "gamma")
PATTERNS = (*NAMES, "*", "a*", "b*")

patterns = st.lists(st.sampled_from(PATTERNS), max_size=2).map(tuple)
policies = st.builds(
    ToolAccessPolicy,
    allow_list=patterns,
    deny_list=patterns,
    approval_list=patterns,
    approval_timeout_seconds=st.sampled_from((30, 300)),
    approval_channel=st.sampled_from(("", "slack")),
)
kinds = st.sampled_from(POLICY_KINDS)
servers = st.sampled_from(SERVERS)
groups = st.sampled_from(GROUPS)
tenants = st.sampled_from(TENANTS)
names = st.sampled_from(NAMES)


@dataclass(frozen=True)
class Registration:
    """One policy set on the resolver, the way configuration or REST sets it."""

    table: str
    key: tuple[Any, ...]
    policy: ToolAccessPolicy

    def apply(self, resolver: ToolAccessResolver) -> None:
        if self.table == "server":
            resolver.set_mcp_server_policy(self.key[0], self.policy, kind=self.key[1])
        elif self.table == "group":
            resolver.set_group_policy(self.key[0], self.policy, kind=self.key[1])
        elif self.table == "member":
            group_id, member, mapped, kind = self.key
            resolver.set_member_policy(group_id, member, self.policy, mcp_server_id=mapped, kind=kind)
        else:
            resolver.set_standalone_member_policy(self.key[0], self.key[1], self.policy, kind=self.key[2])


registrations = st.one_of(
    st.builds(Registration, st.just("server"), st.tuples(st.sampled_from((*SERVERS, "_global")), kinds), policies),
    st.builds(Registration, st.just("group"), st.tuples(groups, kinds), policies),
    st.builds(Registration, st.just("member"), st.tuples(groups, servers, servers, kinds), policies),
    st.builds(Registration, st.just("tenant"), st.tuples(servers, st.sampled_from(TENANTS[1:]), kinds), policies),
)


@dataclass(frozen=True)
class Withdrawal:
    """One withdrawal, declared by configuration or decided at runtime."""

    runtime: bool
    scope: str
    name: str
    tenant_id: str | None
    kind: str

    def apply(self, registry: ToolProjectionRegistry) -> None:
        if self.runtime:
            registry.withdraw(self.scope, self.name, self.tenant_id, kind=self.kind)
        else:
            registry.set_config_withdrawal(self.scope, self.name, self.tenant_id, kind=self.kind)


withdrawals = st.builds(Withdrawal, st.booleans(), st.sampled_from((*SERVERS, *GROUPS)), names, tenants, kinds)


@dataclass(frozen=True)
class Discovery:
    """A server's discovered tools, with the statuses discovery recorded."""

    server: str
    tools: tuple[str, ...]
    withdrawn_at_discovery: tuple[str, ...]
    withdrawn_for: tuple[tuple[str, str], ...]

    def apply(self, registry: ToolProjectionRegistry) -> None:
        overrides: dict[str, dict[str, str]] = {}
        for tool, tenant_id in self.withdrawn_for:
            overrides.setdefault(tool, {})[tenant_id] = "withdrawn"
        registry.build_from_tools(
            self.server,
            [ToolSchema(name=t, description="d", input_schema={"type": "object"}) for t in self.tools],
            status_overrides=dict.fromkeys(self.withdrawn_at_discovery, "withdrawn"),
            tenant_overrides=overrides,
        )


discoveries = st.builds(
    Discovery,
    servers,
    st.lists(names, unique=True, max_size=3).map(tuple),
    st.lists(names, unique=True, max_size=1).map(tuple),
    st.lists(st.tuples(names, st.sampled_from(TENANTS[1:])), max_size=2).map(tuple),
)


def _targets() -> st.SearchStrategy[Target]:
    owners = st.lists(groups, unique=True, max_size=2).map(tuple)
    return st.one_of(
        st.builds(Target.server, servers, owning_groups=st.just(()), kind=kinds),
        st.builds(Target.server, servers, owning_groups=owners, kind=kinds),
        st.builds(Target.group, groups, member_server=servers, kind=kinds),
        st.builds(Target.group, groups, member_server=st.none(), kind=kinds),
    )


@dataclass(frozen=True)
class World:
    """One configuration, one target, one caller, one name."""

    topology: str
    registrations: tuple[Registration, ...]
    discoveries: tuple[Discovery, ...]
    withdrawals: tuple[Withdrawal, ...]
    target: Target
    tenant_id: str | None
    name: str

    def build(self) -> tuple[ToolAccessResolver, ToolProjectionRegistry]:
        resolver = ToolAccessResolver()
        resolver.set_topology_mode(self.topology)  # type: ignore[arg-type]
        for registration in self.registrations:
            registration.apply(resolver)
        registry = ToolProjectionRegistry()
        for discovery in self.discoveries:
            discovery.apply(registry)
        for withdrawal in self.withdrawals:
            withdrawal.apply(registry)
        return resolver, registry


worlds = st.builds(
    World,
    st.sampled_from(("egress", "front_door")),
    st.lists(registrations, max_size=6).map(tuple),
    st.lists(discoveries, max_size=3).map(tuple),
    st.lists(withdrawals, max_size=4).map(tuple),
    _targets(),
    tenants,
    names,
)


def _the_executor_decides(
    resolver: ToolAccessResolver, registry: ToolProjectionRegistry, target: Target, name: str, tenant_id: str | None
) -> Decision:
    """The executor's composition, as `_decide_governance` and the gates make it."""
    routed_to = target.member_server or ""
    scopes = executor._policy_scopes(target.addressed, target.is_group, routed_to, target.owning_groups)
    if not executor._allowed_in_every_scope(resolver, name, tenant_id, scopes):
        return Decision.refused_by(Gate.TOOL_ACCESS)
    projection = executor._resolve_projection(registry, target.addressed, name, tenant_id, routed_to)
    if executor._withdrawn_in_scope(registry, projection, name, tenant_id, target.owning_groups):
        return Decision.refused_by(Gate.WITHDRAWAL)
    return Decision.allow(approval_policy=executor._approval_policy(resolver, name, tenant_id, scopes))


def _the_front_door_decides(
    resolver: ToolAccessResolver, registry: ToolProjectionRegistry, target: Target, name: str, tenant_id: str | None
) -> Decision:
    """The two halves of `is_governed_allowed`, under the call's scopes, for a prompt or a resource."""
    scopes = executor._policy_scopes(
        target.addressed, target.is_group, target.member_server or "", target.owning_groups
    )
    if not all(
        resolver.is_allowed(s, name, kind=target.kind, group_id=g, member_id=tenant_id, member_server_id=m)
        for s, g, m in scopes
    ):
        return Decision.refused_by(Gate.TOOL_ACCESS)
    declared_under = (target.addressed, *(() if target.member_server is None else (target.member_server,)))
    if any(
        registry.is_withdrawn(scope, name, kind=target.kind, tenant_id=tenant_id)
        for scope in (*declared_under, *target.owning_groups)
    ):
        return Decision.refused_by(Gate.WITHDRAWAL)
    for s, g, m in scopes:
        scoped = resolver.resolve_effective_policy(s, g, tenant_id, member_server_id=m, kind=target.kind)
        if not scoped.is_unrestricted() and scoped.requires_approval(name):
            return Decision.allow(approval_policy=scoped)
    return Decision.allow()


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow], database=None)
@given(worlds)
def test_decide_gives_the_answer_the_gates_give(world: World) -> None:
    resolver, registry = world.build()
    caller = Caller(tenant_id=world.tenant_id)
    oracle = _the_executor_decides if world.target.kind == "tool" else _the_front_door_decides

    expected = oracle(resolver, registry, world.target, world.name, world.tenant_id)
    actual = decide(world.target, world.name, caller, resolver=resolver, withdrawals=registry)

    assert (actual.allowed, actual.gate, actual.reason) == (expected.allowed, expected.gate, expected.reason)
    # The same object out of the resolver's cache, not merely an equal one: the
    # approval gate reads its timeout and channel off whichever scope answered.
    assert actual.approval_policy is expected.approval_policy


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow], database=None)
@given(worlds)
def test_decide_leaves_the_resolver_cache_keyed_as_the_executor_keys_it(world: World) -> None:
    """The scope keys `decide()` produces are the ones the executor produced, so invalidation is unchanged."""
    assume(world.target.kind == "tool")
    resolver, registry = world.build()
    _the_executor_decides(resolver, registry, world.target, world.name, world.tenant_id)
    keyed_by_the_executor = set(resolver._policy_cache)

    resolver.invalidate_cache()
    decide(world.target, world.name, Caller(tenant_id=world.tenant_id), resolver=resolver, withdrawals=registry)

    assert set(resolver._policy_cache) == keyed_by_the_executor
