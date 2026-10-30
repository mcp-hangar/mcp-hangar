"""`hangar_details` on a group discloses to a tenant only the members its calls may be routed to (#2019).

A tenant pinned to one member of a group was shown every member, though its
calls only ever went to the one. The decision on #2019: for a tenant, the
members disclosed are `group.route_candidates(tenant_id)`, the set
`hangar_tools` on a group decides on, and every count, the state and
`is_available` describe those members alone. A caller without a tenant (auth
off) is shown the whole group, as before. An empty candidate set is an empty
member list, not an error.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.mcp_server_group import CanaryPolicy, McpServerGroup
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.server.tools import mcp_server as tools

GROUP, ALPHA, BETA, GAMMA = "pool", "alpha", "beta", "gamma"
PINNED, ROTATION = "tenant-one", "tenant-lb"


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


def _server(server_id: str, state: McpServerState = McpServerState.COLD) -> McpServer:
    server = McpServer(mcp_server_id=server_id, mode="subprocess", command=["unused"], metrics_publisher=Mock())
    server._state = state
    return server


def _pool(
    *in_rotation: str,
    ready: tuple[str, ...] = (),
    pinned: dict[str, str] | None = None,
    min_healthy: int = 1,
) -> McpServerGroup:
    """`alpha`, `beta` and `gamma`; *in_rotation* names the routable ones, *ready* the ready ones."""
    group = McpServerGroup(GROUP, auto_start=False, min_healthy=min_healthy)
    for server_id in (ALPHA, BETA, GAMMA):
        group.add_member(_server(server_id, McpServerState.READY if server_id in ready else McpServerState.COLD))
        group.get_member(server_id).in_rotation = server_id in in_rotation  # type: ignore[union-attr]
    if pinned:
        group.set_canary_policy(CanaryPolicy(pinned_tenants=pinned))
    with group._lock:
        group._update_state()
    return group


def _hangar_details() -> Callable[[str], dict[str, Any]]:
    """`hangar_details` as `register_mcp_server_tools` registers it."""
    registered: dict[str, Any] = {}

    class _Mcp:
        def tool(self, name: str | None = None, **_kwargs: Any) -> Any:
            def register(fn: Any) -> Any:
                registered[name or fn.__name__] = fn
                return fn

            return register

    tools.register_mcp_server_tools(_Mcp())  # type: ignore[arg-type]
    return registered["hangar_details"]


def _details(group: McpServerGroup, tenant_id: str | None) -> dict[str, Any]:
    ctx = SimpleNamespace(group_exists=lambda gid: gid == GROUP, get_group=lambda _gid: group)
    with _as(tenant_id), patch.object(tools, "get_context", return_value=ctx):
        return _hangar_details()(GROUP)


def _members(details: dict[str, Any]) -> list[str]:
    return [member["id"] for member in details["members"]]


class TestAPinnedTenant:
    def test_is_shown_only_the_member_it_is_pinned_to(self) -> None:
        details = _details(_pool(ALPHA, BETA, ready=(ALPHA, BETA), pinned={PINNED: ALPHA}), PINNED)

        assert _members(details) == [ALPHA]

    def test_the_counts_do_not_reveal_the_hidden_members(self) -> None:
        details = _details(_pool(ALPHA, BETA, ready=(ALPHA, BETA), pinned={PINNED: ALPHA}), PINNED)

        assert details["total_members"] == 1
        assert details["healthy_count"] == 1
        assert details["members_in_rotation_count"] == 1

    def test_the_state_is_judged_on_the_members_it_is_shown(self) -> None:
        """min_healthy=2 is capped at the one member shown: the tenant's calls are served, so it is healthy."""
        group = _pool(ALPHA, BETA, ready=(ALPHA, BETA), pinned={PINNED: ALPHA}, min_healthy=2)

        assert _details(group, None)["state"] == "healthy"
        details = _details(group, PINNED)
        assert details["state"] == "healthy"
        assert details["is_available"] is True

    def test_min_healthy_and_the_group_breaker_are_not_shown(self) -> None:
        """Both describe the whole group: one counts hidden members, the other opens on their failures."""
        details = _details(_pool(ALPHA, BETA, ready=(ALPHA, BETA), pinned={PINNED: ALPHA}, min_healthy=2), PINNED)

        assert "min_healthy" not in details
        assert "circuit_open" not in details

    def test_a_dead_pinned_member_is_not_healthy_whatever_the_others_are(self) -> None:
        group = _pool(ALPHA, BETA, ready=(BETA,), pinned={PINNED: ALPHA})
        group.get_member(ALPHA).mcp_server._state = McpServerState.DEAD  # type: ignore[union-attr]

        details = _details(group, PINNED)

        assert _members(details) == [ALPHA]
        assert details["state"] == "inactive"
        assert details["is_available"] is False
        assert details["healthy_count"] == 0


class TestATenantInRotation:
    def test_is_shown_every_member_in_rotation_and_no_benched_one(self) -> None:
        details = _details(_pool(ALPHA, BETA, ready=(ALPHA,), pinned={PINNED: ALPHA}), ROTATION)

        assert _members(details) == [ALPHA, BETA]
        assert details["total_members"] == details["members_in_rotation_count"] == 2
        assert details["healthy_count"] == 1


class TestNoCandidate:
    def test_an_empty_candidate_set_is_an_empty_member_list(self) -> None:
        details = _details(_pool(pinned={PINNED: ALPHA}), PINNED)

        assert details["members"] == []
        assert details["total_members"] == details["healthy_count"] == details["members_in_rotation_count"] == 0
        assert details["state"] == "inactive"
        assert details["is_available"] is False


class TestNoTenant:
    @pytest.mark.parametrize("in_rotation", [(ALPHA, BETA), (ALPHA,), ()])
    def test_the_whole_group_is_shown_as_before(self, in_rotation: tuple[str, ...]) -> None:
        group = _pool(*in_rotation, ready=(ALPHA,), pinned={PINNED: ALPHA})

        details = _details(group, None)
        assert details == group.to_status_dict()
        assert list(details) == [
            "group_id",
            "description",
            "state",
            "strategy",
            "min_healthy",
            "healthy_count",
            "members_in_rotation_count",
            "total_members",
            "is_available",
            "circuit_open",
            "members",
        ]
        assert _members(_details(group, None)) == [ALPHA, BETA, GAMMA]
