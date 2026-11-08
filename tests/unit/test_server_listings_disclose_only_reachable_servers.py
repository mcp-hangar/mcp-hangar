"""A tenant's server listings show only the servers its calls may reach (#2161).

After #2155 a tenant's group views list only the members its calls may be
routed to. The server listings did not: the `mcp_servers` section of
`hangar_list`, and `hangar_status` and `hangar_health`, listed every configured
server to every tenant, the group members declared at top level included, and
`hangar_start` on a group and the group counts of `hangar_status` and
`hangar_health` were whole-group. The owner's decisions on #2161:

1. a tenant's server listing shows the servers no group owns plus the group
   members it may be routed to, the rule of the group views;
2. `hangar_group_rebalance` stays a whole-group operator action: a tenant
   caller is refused;
3. a tenant's group counts are over its reachable members and carry no
   `circuit_open`;
4. with no tenant, the output is what it was.

Naming: neutral placeholders only (pool, alpha, beta, gamma, delta, tenant-*).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.exceptions import AccessDeniedError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.mcp_server_group import CanaryPolicy, McpServerGroup
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.server.tools import groups as group_tools
from mcp_hangar.server.tools import hangar as hangar_tools
from mcp_hangar.server.tools import health as health_tools
from mcp_hangar.server.tools import replica_view

GROUP, ALPHA, BETA, GAMMA, DELTA = "pool", "alpha", "beta", "gamma", "delta"
PINNED, ROTATION = "tenant-one", "tenant-lb"
#: Every configured server, in config order: the three members of `pool` and `delta`, which no group owns.
EVERY_SERVER = [ALPHA, BETA, GAMMA, DELTA]


@contextmanager
def _as(tenant_id: str | None) -> Iterator[None]:
    caller = CallerIdentity(
        user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant_id
    )
    token = identity_context_var.set(IdentityContext(caller=caller) if tenant_id is not None else None)
    try:
        yield
    finally:
        identity_context_var.reset(token)


def _server(server_id: str, state: McpServerState) -> McpServer:
    server = McpServer(mcp_server_id=server_id, mode="subprocess", command=["unused"], metrics_publisher=Mock())
    server._state = state
    return server


class _Fleet:
    """`alpha`, `beta` ready in rotation and `gamma` cold and benched, in `pool`; `delta` alone.

    `tenant-one` is pinned to `alpha`.
    """

    def __init__(self) -> None:
        self.servers = {
            sid: _server(sid, McpServerState.COLD if sid == GAMMA else McpServerState.READY) for sid in EVERY_SERVER
        }
        self.group = McpServerGroup(GROUP, auto_start=False, min_healthy=1)
        for sid in (ALPHA, BETA, GAMMA):
            self.group.add_member(self.servers[sid])
            self.group.get_member(sid).in_rotation = sid != GAMMA  # type: ignore[union-attr]
        self.group.set_canary_policy(CanaryPolicy(pinned_tenants={PINNED: ALPHA}))
        with self.group._lock:
            self.group._update_state()
        self.group.start_all = lambda: 2  # type: ignore[method-assign]  # nothing to launch here
        self.group.rebalance = Mock()  # type: ignore[method-assign]
        #: What `hangar_health` reads as the required catalogue; none in force by default.
        self.catalogue: dict[str, Any] | None = None
        summaries = [
            SimpleNamespace(mcp_server_id=sid, to_dict=lambda sid=sid: {"mcp_server_id": sid}) for sid in EVERY_SERVER
        ]
        self.ctx = SimpleNamespace(
            groups={GROUP: self.group},
            repository=SimpleNamespace(get_all=lambda: dict(self.servers)),
            query_bus=SimpleNamespace(execute=lambda _query: summaries),
            group_exists=lambda gid: gid == GROUP,
            get_group=lambda gid: self.group if gid == GROUP else None,
            rate_limiter=SimpleNamespace(get_stats=lambda: {}),
            event_bus=Mock(),
        )

    def call(self, tenant_id: str | None, fn: Callable[..., Any], *args: Any) -> Any:
        runtime = SimpleNamespace(list_all=lambda: [])
        with ExitStack() as stack:
            stack.enter_context(_as(tenant_id))
            for module in (hangar_tools, health_tools, replica_view, group_tools):
                stack.enter_context(patch.object(module, "get_context", return_value=self.ctx))
            stack.enter_context(patch("mcp_hangar.server.state.get_runtime_mcp_servers", return_value=runtime))
            stack.enter_context(patch.object(group_tools, "publish_group_events"))
            stack.enter_context(patch.object(health_tools, "catalogue_detail", return_value=self.catalogue))
            stack.enter_context(patch.object(health_tools, "get_compliance_export_status", return_value=None))
            return fn(*args)


def _registered(register: Callable[[Any], None], name: str) -> Callable[..., Any]:
    registered: dict[str, Any] = {}

    class _Mcp:
        def tool(self, name: str | None = None, **_kwargs: Any) -> Any:
            def add(fn: Any) -> Any:
                registered[name or fn.__name__] = fn
                return fn

            return add

    register(_Mcp())
    return registered[name]


def _listed(fleet: _Fleet, tenant_id: str | None) -> list[str]:
    return [s["mcp_server_id"] for s in fleet.call(tenant_id, hangar_tools.hangar_list)["mcp_servers"]]


def _status(fleet: _Fleet, tenant_id: str | None) -> dict[str, Any]:
    return fleet.call(tenant_id, hangar_tools.hangar_status)


def _start(fleet: _Fleet, tenant_id: str | None) -> dict[str, Any]:
    return fleet.call(tenant_id, _registered(hangar_tools.register_hangar_tools, "hangar_start"), GROUP)


def _rebalance(fleet: _Fleet, tenant_id: str | None) -> dict[str, Any]:
    return fleet.call(tenant_id, _registered(group_tools.register_group_tools, "hangar_group_rebalance"), GROUP)


class TestAPinnedTenant:
    def test_hangar_list_shows_its_member_and_the_server_no_group_owns(self) -> None:
        assert _listed(_Fleet(), PINNED) == [ALPHA, DELTA]

    def test_hangar_status_lists_and_counts_only_those(self) -> None:
        status = _status(_Fleet(), PINNED)

        assert [s["id"] for s in status["mcp_servers"]] == [ALPHA, DELTA]
        assert status["summary"]["total_mcp_servers"] == status["summary"]["healthy_mcp_servers"] == 2
        assert status["groups"] == [
            {
                "id": GROUP,
                "indicator": "[HEALTHY]",
                "state": "healthy",
                "healthy_members": 1,
                "members_in_rotation_count": 1,
                "total_members": 1,
            }
        ]
        assert BETA not in status["formatted"] and GAMMA not in status["formatted"]
        assert "1/1" in status["formatted"] and "closed" not in status["formatted"]

    def test_hangar_health_counts_only_those(self) -> None:
        health = _Fleet().call(PINNED, health_tools.hangar_health)

        assert health["mcp_servers"] == {"total": 2, "by_state": {"ready": 2}}
        assert health["groups"]["total_members"] == health["groups"]["healthy_members"] == 1
        assert health["groups"]["members_in_rotation_count"] == 1

    def test_hangar_start_on_the_group_reports_its_member(self) -> None:
        assert _start(_Fleet(), PINNED) == {
            "group": GROUP,
            "state": "healthy",
            "members_started": 1,
            "healthy_count": 1,
            "members_in_rotation_count": 1,
            "total_members": 1,
        }


def test_hangar_health_names_no_hidden_member_in_the_catalogue() -> None:
    """The required catalogue names servers too; a tenant is not told of a member it is not shown."""
    fleet = _Fleet()
    detail = {
        "complete": False,
        "holds_readiness": True,
        "required": [BETA, DELTA, GROUP],
        "missing": [BETA],
        "not_retried": {BETA: "capability_blocked"},
        "retry": "not_started",
    }

    fleet.catalogue = detail
    pinned = fleet.call(PINNED, health_tools.hangar_health)["catalogue"]
    whole = fleet.call(None, health_tools.hangar_health)["catalogue"]

    assert pinned == {**detail, "required": [DELTA, GROUP], "missing": [], "not_retried": {}}
    assert whole == detail


class TestATenantInRotation:
    def test_is_shown_every_member_in_rotation_and_no_benched_one(self) -> None:
        fleet = _Fleet()

        assert _listed(fleet, ROTATION) == [ALPHA, BETA, DELTA]
        assert [s["id"] for s in _status(fleet, ROTATION)["mcp_servers"]] == [ALPHA, BETA, DELTA]
        assert _start(fleet, ROTATION)["total_members"] == 2

    def test_an_empty_tenant_is_a_tenant(self) -> None:
        assert _listed(_Fleet(), "") == [ALPHA, BETA, DELTA]


class TestRebalance:
    def test_a_tenant_caller_is_refused_and_nothing_is_rebalanced(self) -> None:
        fleet = _Fleet()

        refusal = _rebalance(fleet, PINNED)

        assert refusal["error_type"] == AccessDeniedError.__name__
        assert "rebalance" in refusal["error"] and BETA not in refusal["error"]
        fleet.group.rebalance.assert_not_called()  # type: ignore[attr-defined]

    def test_a_caller_without_a_tenant_rebalances_the_whole_group(self) -> None:
        fleet = _Fleet()

        assert _rebalance(fleet, None)["members_in_rotation"] == [ALPHA, BETA]
        fleet.group.rebalance.assert_called_once()  # type: ignore[attr-defined]


class TestNoTenant:
    """The output a caller without a tenant is shown, literally: what it was before #2161."""

    def test_hangar_list_lists_every_server(self) -> None:
        assert _listed(_Fleet(), None) == EVERY_SERVER

    def test_hangar_status_is_unchanged(self) -> None:
        status = _status(_Fleet(), None)

        assert [s["id"] for s in status["mcp_servers"]] == EVERY_SERVER
        assert status["summary"]["total_mcp_servers"] == 4
        assert status["summary"]["healthy_mcp_servers"] == 3
        assert [list(g.items()) for g in status["groups"]] == [
            [
                ("id", GROUP),
                ("indicator", "[HEALTHY]"),
                ("state", "healthy"),
                ("healthy_members", 2),
                ("members_in_rotation_count", 2),
                ("total_members", 3),
                ("circuit_open", False),
            ]
        ]
        assert "2/3" in status["formatted"] and "closed" in status["formatted"]

    def test_hangar_health_is_unchanged(self) -> None:
        health = _Fleet().call(None, health_tools.hangar_health)

        assert health["mcp_servers"] == {"total": 4, "by_state": {"ready": 3, "cold": 1}}
        assert health["groups"] == {
            "total": 1,
            "by_state": {"healthy": 1},
            "total_members": 3,
            "healthy_members": 2,
            "members_in_rotation_count": 2,
        }

    def test_hangar_start_on_the_group_is_unchanged(self) -> None:
        assert list(_start(_Fleet(), None).items()) == [
            ("group", GROUP),
            ("state", "healthy"),
            ("members_started", 2),
            ("healthy_count", 2),
            ("members_in_rotation_count", 2),
            ("total_members", 3),
        ]


def test_a_member_of_two_groups_is_shown_when_either_routes_the_tenant_to_it() -> None:
    fleet = _Fleet()
    other = McpServerGroup("other", auto_start=False, min_healthy=1)
    other.add_member(fleet.servers[BETA])
    other.get_member(BETA).in_rotation = True  # type: ignore[union-attr]
    fleet.ctx.groups["other"] = other

    assert _listed(fleet, PINNED) == [ALPHA, BETA, DELTA]
    assert fleet.call(PINNED, replica_view.members_hidden_from, PINNED) == frozenset({GAMMA})
    assert fleet.call(None, replica_view.members_hidden_from, None) == frozenset()
