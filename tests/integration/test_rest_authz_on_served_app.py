"""The REST authorization chokepoint must be armed on the app `serve --http` builds.

This repo has shipped the same class of bug four times: a capability wired only
into a construction path that production does not use (MCPServerFactory has no
production call site; the relay serving surface was wired only there, #592). A
unit test over ``create_api_router`` proves the router authorizes -- it does not
prove the *served* application does, because the served app nests that router
under ``Mount("/api")`` inside an aux app, wraps the whole thing in
``create_auth_enforced_app``, and reaches it through a hand-written
``combined_app`` dispatcher.

Every layer in that stack can break the guard in a way the unit tests cannot
see:

* the outer ``Mount("/api")`` rewrites ``scope["path"]``, so a table keyed on
  the unstripped path would match nothing and deny everything;
* the outer ``AuthEnforcementMiddleware`` stores auth context as a ``State``
  object while the router's ``AuthMiddlewareHTTP`` stores a plain ``dict``, so
  reading one shape makes every request look unauthenticated;
* ``combined_app`` routes ``/api`` by string prefix before any middleware in the
  router runs.

So this test assembles the stack the way ``ServerLifecycle.run_http`` assembles
it and drives it end to end. It is deliberately a near-copy of that code: if the
assembly in ``lifecycle.py`` changes shape, this test should be updated in the
same commit, and the diff makes that visible.
"""

from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from mcp_hangar.auth.infrastructure.middleware import AuthorizationMiddleware
from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.server.api import create_api_router
from mcp_hangar.server.api.middleware import create_auth_enforced_app


def _auth_components(principal: Principal, role: str | None) -> SimpleNamespace:
    store = InMemoryRoleStore()
    if role is not None:
        store.assign_role(principal_id=str(principal.id), role_name=role)
    return SimpleNamespace(
        enabled=True,
        authn_middleware=SimpleNamespace(
            authenticate=lambda _request: SimpleNamespace(principal=principal),
        ),
        authz_middleware=AuthorizationMiddleware(authorizer=RBACAuthorizer(store)),
        oidc_issuers=[],
        oidc_resource_uri="",
    )


def _served_app(role: str | None, principal_name: str = "user:alice"):
    """Rebuild the ASGI stack that ServerLifecycle.run_http serves.

    Mirrors lifecycle.py: health routes + Mount("/api", api_app) in an aux app,
    a combined_app dispatcher choosing between aux and the MCP app by path
    prefix, and the whole thing wrapped by create_auth_enforced_app.
    """
    principal = Principal(id=PrincipalId(principal_name), type=PrincipalType.USER) if role else Principal.anonymous()
    auth_components = _auth_components(principal, role)

    api_app = create_api_router(auth_components=auth_components)

    async def live(_request):
        return JSONResponse({"status": "ok"})

    aux_app = Starlette(
        routes=[
            Route("/health/live", live, methods=["GET"]),
            Mount("/api", app=api_app),
        ]
    )

    async def mcp_app(scope, receive, send):
        await JSONResponse({"surface": "mcp"})(scope, receive, send)

    async def combined_app(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path.startswith("/health/") or path == "/api" or path.startswith("/api/"):
                await aux_app(scope, receive, send)
                return
        await mcp_app(scope, receive, send)

    return create_auth_enforced_app(combined_app, auth_components)


@pytest.fixture
def client_factory(monkeypatch):
    """A client whose *handlers* can also see the auth components.

    The router is built with an `auth_components` object passed in, but the
    handlers' own `_check_permission` reads them from the global application
    context instead. Without wiring that up, every in-handler check in the API
    short-circuits on `not getattr(auth_components, "enabled", False)` and this
    file tests only half the stack -- which is how a handler demanding a
    permission the route table does not could sit here unnoticed.
    """

    def make(role: str | None):
        principal = Principal(id=PrincipalId("user:alice"), type=PrincipalType.USER) if role else Principal.anonymous()
        components = _auth_components(principal, role)
        monkeypatch.setattr(
            "mcp_hangar.server.api.mcp_servers.get_context",
            lambda: SimpleNamespace(auth_components=components),
        )
        return TestClient(_served_app(role), raise_server_exceptions=False)

    return make


class TestChokepointIsArmedOnTheServedApp:
    """The guard survives the /api mount, the dispatcher and the outer wrapper."""

    def test_auth_admin_surface_is_denied_to_non_admin(self, client_factory):
        """The P0: any valid credential could self-grant admin through /api/auth."""
        client = client_factory("developer")
        response = client.post(
            "/api/auth/roles/assign",
            json={"principal_id": "user:alice", "role_name": "admin"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "AccessDeniedError"

    def test_config_reload_is_denied_to_non_admin(self, client_factory):
        client = client_factory("developer")
        response = client.post("/api/config/reload", json={})
        assert response.status_code == 403

    def test_l7_policy_is_denied_to_developer(self, client_factory):
        """ADR-013 channel stays admin-only through the full stack."""
        client = client_factory("developer")
        response = client.delete("/api/mcp_servers/srv1/l7_policy")
        assert response.status_code == 403

    def test_unmapped_api_route_is_denied(self, client_factory):
        """Fail-closed default survives the mount prefix rewrite."""
        client = client_factory("admin")
        response = client.get("/api/nothing-here")
        assert response.status_code == 403


class TestGuardDoesNotOverreach:
    """Denying things that must stay reachable would be its own outage."""

    def test_health_stays_public(self, client_factory):
        """Liveness must answer before any credential exists."""
        client = client_factory(None)
        response = client.get("/health/live")
        assert response.status_code == 200

    def test_permitted_call_passes_the_gate(self, client_factory):
        """A developer reading servers is not blocked.

        The handler may fail downstream for want of a wired context; the
        assertion is specifically that it is not an authz rejection.
        """
        client = client_factory("developer")
        response = client.get("/api/mcp_servers")
        assert response.status_code not in (401, 403)

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", "/api/mcp_servers/srv1/l7_policy"),
            ("DELETE", "/api/mcp_servers/srv1/l7_policy"),
        ],
    )
    def test_provider_admin_reaches_the_egress_policy_channel(self, client_factory, method, path):
        """The role that exists to deliver compiled policy must get through.

        `test_operator_role_compatibility.py` already pins this contract, but it
        pins it against `resolve_rule` and the role definitions -- the table and
        the roles agreed, and the *handler* disagreed with both: it demanded
        `mcp_servers:write` on top, which `provider-admin` does not hold. So the
        operator's push answered 403 while every test of the contract passed.

        Asserted through the served stack for that reason: only the request path
        sees both the table and the handler.
        """
        client = client_factory("provider-admin")

        response = client.request(method, path, json={"default_action": "deny"})

        assert response.status_code not in (401, 403), response.text

    def test_non_api_paths_reach_the_mcp_surface(self, client_factory):
        """combined_app still routes everything else to the MCP app."""
        client = client_factory("developer")
        response = client.get("/anything-else")
        assert response.status_code == 200
        assert response.json() == {"surface": "mcp"}


class TestPathRewriteIsHandled:
    """Regression: the table is keyed on the path AFTER Mount('/api') strips it."""

    def test_admin_reaches_the_auth_surface_through_the_mount(self, client_factory):
        client = client_factory("admin")
        response = client.get("/api/auth/keys")
        assert response.status_code not in (401, 403)

    def test_denial_is_by_permission_not_by_path_mismatch(self, client_factory):
        """A viewer is denied /api/groups POST because of group:create.

        If the table failed to match the rewritten path, this would still be a
        403 -- but so would the admin case above, which passes. The pair
        distinguishes "denied for the right reason" from "denies everything".
        """
        client = client_factory("viewer")
        assert client.post("/api/groups", json={}).status_code == 403
        assert client.get("/api/groups").status_code not in (401, 403)
