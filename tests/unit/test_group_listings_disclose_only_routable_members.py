"""Every group listing discloses to a tenant only the members its calls may be routed to (#2155).

#2019 cut `hangar_details` on a group down to `group.route_candidates(tenant)`
for a caller with a tenant. `hangar_list`, `hangar_group_list`,
`GET /api/groups` and `GET /api/groups/{id}` kept returning
`group.to_status_dict()`, so a tenant pinned to one member was still shown
every member, with its id, state and counts. The decision on #2155: every one
of them gives a tenant the view `hangar_details` gives
(`McpServerGroup.status_for_tenant`), the REST routes included; a caller
without a tenant is shown the whole group, byte for byte as before.

The REST routes are driven through the stack `serve --http` serves (the
`/api` mount inside `create_auth_enforced_app`), so the principal is read where
the real authentication leaves it, not from a hand-built scope.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from mcp_hangar.auth.infrastructure.middleware import AuthorizationMiddleware
from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.mcp_server_group import CanaryPolicy, McpServerGroup
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.server.api import create_api_router
from mcp_hangar.server.api.middleware import create_auth_enforced_app
from mcp_hangar.server.tools import groups as group_tools
from mcp_hangar.server.tools import hangar as hangar_tools

GROUP, ALPHA, BETA, GAMMA = "pool", "alpha", "beta", "gamma"
PINNED, ROTATION = "tenant-one", "tenant-lb"

#: The keys a caller without a tenant is shown, in their order: unchanged by #2155.
WHOLE_GROUP_KEYS = [
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


def _pool(min_healthy: int = 1) -> McpServerGroup:
    """`alpha` and `beta` ready and in rotation, `gamma` cold and benched; `tenant-one` pinned to `alpha`."""
    group = McpServerGroup(GROUP, auto_start=False, min_healthy=min_healthy)
    for server_id in (ALPHA, BETA, GAMMA):
        group.add_member(_server(server_id, McpServerState.COLD if server_id == GAMMA else McpServerState.READY))
        group.get_member(server_id).in_rotation = server_id != GAMMA  # type: ignore[union-attr]
    group.set_canary_policy(CanaryPolicy(pinned_tenants={PINNED: ALPHA}))
    with group._lock:
        group._update_state()
    return group


def _registered(register: Callable[[Any], None], name: str) -> Callable[..., dict[str, Any]]:
    """The tool *name* as *register* registers it."""
    registered: dict[str, Any] = {}

    class _Mcp:
        def tool(self, name: str | None = None, **_kwargs: Any) -> Any:
            def add(fn: Any) -> Any:
                registered[name or fn.__name__] = fn
                return fn

            return add

    register(_Mcp())
    return registered[name]


def _ctx(group: McpServerGroup) -> SimpleNamespace:
    return SimpleNamespace(
        groups={GROUP: group},
        query_bus=SimpleNamespace(execute=lambda _query: []),
        group_exists=lambda gid: gid == GROUP,
        get_group=lambda gid: group if gid == GROUP else None,
    )


def _hangar_list(group: McpServerGroup, tenant_id: str | None, state_filter: str | None = None) -> dict[str, Any]:
    runtime = SimpleNamespace(list_all=lambda: [])
    with (
        _as(tenant_id),
        patch.object(hangar_tools, "get_context", return_value=_ctx(group)),
        patch("mcp_hangar.server.state.get_runtime_mcp_servers", return_value=runtime),
    ):
        return hangar_tools.hangar_list(state_filter)


def _hangar_group_list(group: McpServerGroup, tenant_id: str | None) -> dict[str, Any]:
    tool = _registered(group_tools.register_group_tools, "hangar_group_list")
    with _as(tenant_id), patch.object(group_tools, "get_context", return_value=_ctx(group)):
        return tool()


def _rest(group: McpServerGroup, tenant_id: str | None, path: str) -> Any:
    """GET *path* through the `/api` mount and the auth wrapper `serve --http` serves, as a viewer of *tenant_id*."""
    principal = Principal(id=PrincipalId("user:viewer"), type=PrincipalType.USER, tenant_id=tenant_id)
    store = InMemoryRoleStore()
    store.assign_role(principal_id=str(principal.id), role_name="viewer")
    components = SimpleNamespace(
        enabled=True,
        authn_middleware=SimpleNamespace(authenticate=lambda _request: SimpleNamespace(principal=principal)),
        authz_middleware=AuthorizationMiddleware(authorizer=RBACAuthorizer(store)),
        oidc_issuers=[],
        oidc_resource_uri="",
    )
    app = create_auth_enforced_app(
        Starlette(routes=[Mount("/api", app=create_api_router(auth_components=components))]), components
    )
    with patch("mcp_hangar.server.api.groups.get_context", return_value=_ctx(group)):
        return TestClient(app, raise_server_exceptions=False).get(path)


def _listed(listing: dict[str, Any]) -> dict[str, Any]:
    (entry,) = [g for g in listing["groups"] if g["group_id"] == GROUP]
    return entry


SURFACES: dict[str, Callable[[McpServerGroup, str | None], dict[str, Any]]] = {
    "hangar_list": lambda group, tenant: _listed(_hangar_list(group, tenant)),
    "hangar_group_list": lambda group, tenant: _listed(_hangar_group_list(group, tenant)),
    "GET /api/groups": lambda group, tenant: _listed(_rest(group, tenant, "/api/groups").json()),
    "GET /api/groups/{id}": lambda group, tenant: _rest(group, tenant, f"/api/groups/{GROUP}").json(),
}


def _members(status: dict[str, Any]) -> list[str]:
    return [member["id"] for member in status["members"]]


@pytest.mark.parametrize("surface", SURFACES)
class TestAPinnedTenant:
    def test_is_shown_only_the_member_it_is_pinned_to(self, surface: str) -> None:
        assert _members(SURFACES[surface](_pool(), PINNED)) == [ALPHA]

    def test_the_counts_and_state_are_judged_on_that_member(self, surface: str) -> None:
        """Two live members against min_healthy=2 is healthy; the one shown, capped at one, is healthy too."""
        status = SURFACES[surface](_pool(min_healthy=2), PINNED)

        assert status["total_members"] == status["healthy_count"] == status["members_in_rotation_count"] == 1
        assert status["state"] == "healthy"
        assert status["is_available"] is True
        assert "min_healthy" not in status and "circuit_open" not in status

    def test_it_is_the_view_hangar_details_gives(self, surface: str) -> None:
        group = _pool()

        assert SURFACES[surface](group, PINNED) == group.status_for_tenant(PINNED)


@pytest.mark.parametrize("surface", SURFACES)
class TestATenantInRotation:
    def test_is_shown_every_member_in_rotation_and_no_benched_one(self, surface: str) -> None:
        status = SURFACES[surface](_pool(), ROTATION)

        assert _members(status) == [ALPHA, BETA]
        assert status["total_members"] == 2


@pytest.mark.parametrize("surface", SURFACES)
class TestNoTenant:
    def test_the_whole_group_is_shown_as_before(self, surface: str) -> None:
        group = _pool()
        status = SURFACES[surface](group, None)

        assert list(status) == WHOLE_GROUP_KEYS
        assert status == group.to_status_dict()
        assert _members(status) == [ALPHA, BETA, GAMMA]


class TestTheStateFilter:
    def test_hangar_list_filters_on_the_state_the_tenant_is_shown(self) -> None:
        """The whole group is partial (two live, min_healthy=3); the pinned tenant's one-member view is healthy."""
        group = _pool(min_healthy=3)
        assert group.to_status_dict()["state"] == "partial"

        assert [g["group_id"] for g in _hangar_list(group, PINNED, "healthy")["groups"]] == [GROUP]
        assert _hangar_list(group, PINNED, "partial")["groups"] == []
        assert [g["group_id"] for g in _hangar_list(group, None, "partial")["groups"]] == [GROUP]


class TestNoRoutableMember:
    def test_get_api_group_is_the_empty_tenant_view_not_a_404(self) -> None:
        group = _pool()
        for server_id in (ALPHA, BETA):
            group.get_member(server_id).in_rotation = False  # type: ignore[union-attr]

        response = _rest(group, PINNED, f"/api/groups/{GROUP}")

        assert response.status_code == 200
        assert response.json()["members"] == []
        assert response.json()["is_available"] is False
