"""Negative tests for route-driven REST/WebSocket authorization.

Every assertion here is a fail-closed claim made by
``server/api/route_permissions.py`` and enforced by
``AuthorizationEnforcementMiddleware``. They are written as denials first: the
bug class being fixed is "the route made no authorization decision at all", so a
test that only proves the happy path would have passed before the fix too.

Regression anchors:
* ``/auth/*`` was authentication-only -- any valid credential could POST
  ``/auth/roles/assign`` and self-grant ``admin``.
* ``/config``, ``/discovery``, ``/groups``, ``/sessions``, ``/tools`` and the
  ``/approvals`` reads never called ``authorize()``.
* ``/mcp_servers/{id}/l7_policy`` was gated on ``mcp_servers:write``, which
  ``ROLE_DEVELOPER`` holds, so a developer token could clear a compiled egress
  policy that ADR-013 makes admin-only (``policy:write``).
* An unmapped route must deny rather than fall through to the handler.
* Auth disabled must remain fully open -- arming authorization against an app
  that mounts no authentication is how #600 turned "fail closed on the API"
  into "fail open on enforcement".
"""

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from mcp_hangar.auth.infrastructure.middleware import AuthorizationMiddleware
from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.server.api.route_permissions import ROUTE_PERMISSIONS, resolve_rule
from mcp_hangar.server.api.router import create_api_router


def _principal(name: str) -> Principal:
    return Principal(id=PrincipalId(name), type=PrincipalType.USER)


def _auth_components(principal: Principal | None, role: str | None) -> SimpleNamespace:
    """Build enabled auth components whose authenticator yields ``principal``."""
    store = InMemoryRoleStore()
    if principal is not None and role is not None:
        store.assign_role(principal_id=str(principal.id), role_name=role)

    authn = SimpleNamespace(
        authenticate=lambda _request: SimpleNamespace(principal=principal),
    )
    return SimpleNamespace(
        enabled=True,
        authn_middleware=authn,
        authz_middleware=AuthorizationMiddleware(authorizer=RBACAuthorizer(store)),
        oidc_issuers=[],
        oidc_resource_uri="",
    )


def _client(role: str | None, principal_name: str = "user:alice") -> TestClient:
    principal = _principal(principal_name) if role is not None else Principal.anonymous()
    app = create_api_router(auth_components=_auth_components(principal, role))
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# The table itself
# ---------------------------------------------------------------------------


class TestRoutePermissionTable:
    """The table must cover every mounted route, or the route is unreachable."""

    def test_every_mounted_route_resolves_to_a_rule(self):
        """No route mounted by create_api_router may be absent from the table.

        An absent route is denied at runtime, so this failing means either a new
        endpoint shipped without an authorization decision, or a path template
        drifted from the table.
        """
        from starlette.routing import Mount, Route, WebSocketRoute

        app = create_api_router()
        unmapped: list[str] = []

        def walk(routes, prefix: str) -> None:
            for route in routes:
                if isinstance(route, Mount):
                    walk(route.routes, prefix + route.path)
                    continue
                if isinstance(route, WebSocketRoute):
                    full = (prefix + route.path).rstrip("/") or "/"
                    if resolve_rule("GET", full) is None:
                        unmapped.append(f"WS {full}")
                    continue
                if isinstance(route, Route):
                    full = (prefix + route.path).rstrip("/") or "/"
                    for method in sorted(route.methods or set()):
                        if method in {"HEAD", "OPTIONS"}:
                            continue
                        if resolve_rule(method, full) is None:
                            unmapped.append(f"{method} {full}")

        walk(app.routes, "")
        assert unmapped == [], f"routes with no permission mapping: {unmapped}"

    def test_unmapped_path_resolves_to_none(self):
        """The default really is deny, not a catch-all rule."""
        assert resolve_rule("GET", "/not/a/real/route") is None
        assert resolve_rule("POST", "/mcp_servers/abc/definitely-new") is None

    def test_l7_policy_requires_policy_write(self):
        """ADR-013: the egress-policy channel is admin-only, not mcp_servers:write."""
        for method in ("POST", "PUT", "DELETE"):
            rule = resolve_rule(method, "/mcp_servers/srv1/l7_policy")
            assert rule is not None
            assert rule.permission == ("policy", "write")

    def test_config_reload_requires_config_reload(self):
        rule = resolve_rule("POST", "/config/reload")
        assert rule is not None
        assert rule.permission == ("config", "reload")

    def test_auth_subtree_is_admin_only(self):
        for path in (
            "/auth/keys",
            "/auth/roles/assign",
            "/auth/roles/revoke",
            "/auth/policies/provider/srv1",
            "/auth/principals",
        ):
            rule = resolve_rule("POST", path)
            assert rule is not None, path
            assert rule.permission == ("admin", "*"), path

    def test_table_has_no_duplicate_templates(self):
        seen = [rule.template for rule in ROUTE_PERMISSIONS]
        assert len(seen) == len(set(seen)), "duplicate rule templates shadow each other"


# ---------------------------------------------------------------------------
# Denials
# ---------------------------------------------------------------------------


class TestAuthSurfaceIsAdminOnly:
    """Regression: /auth was authentication-only, enabling self-escalation."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", "/auth/roles/assign"),
            ("post", "/auth/keys"),
            ("get", "/auth/keys"),
            ("delete", "/auth/keys/k1"),
            ("post", "/auth/policies/provider/srv1"),
            ("get", "/auth/principals"),
        ],
    )
    def test_non_admin_is_denied(self, method, path):
        client = _client(role="developer")
        kwargs = {"json": {}} if method == "post" else {}
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 403, f"{method.upper()} {path} was not denied"
        assert response.json()["error"] == "access_denied"

    def test_provider_admin_cannot_assign_roles(self):
        """The most privileged non-admin role must not reach role assignment."""
        client = _client(role="provider-admin")
        response = client.post(
            "/auth/roles/assign",
            json={"principal_id": "user:alice", "role_name": "admin"},
        )
        assert response.status_code == 403

    def test_admin_passes_the_authorization_gate(self):
        """Admin must not be blocked by the new gate.

        The handler may still fail for unrelated reasons (no wired role store in
        this app), so the assertion is specifically "not an authz rejection".
        """
        client = _client(role="admin")
        response = client.get("/auth/keys")
        assert response.status_code not in (401, 403)


class TestUnguardedRoutersAreNowGuarded:
    """Regression: five routers made no authorization decision at all."""

    @pytest.mark.parametrize(
        "role,method,path,expected_denied",
        [
            # config:reload is admin-only; nobody else may re-apply governance inputs.
            ("developer", "post", "/config/reload", True),
            ("provider-admin", "post", "/config/reload", True),
            ("admin", "post", "/config/reload", False),
            # discovery approve/reject is provider-admin and above.
            ("viewer", "post", "/discovery/approve/srv1", True),
            ("developer", "post", "/discovery/approve/srv1", True),
            ("provider-admin", "post", "/discovery/approve/srv1", False),
            # group mutation is not a viewer capability.
            ("viewer", "post", "/groups", True),
            ("provider-admin", "post", "/groups", False),
            # session suspend is a lifecycle operation.
            ("viewer", "post", "/sessions/s1/suspend", True),
            ("developer", "post", "/sessions/s1/suspend", False),
        ],
    )
    def test_authorization_matrix(self, role, method, path, expected_denied):
        client = _client(role=role)
        response = getattr(client, method)(path, json={})
        denied = response.status_code == 403
        expectation = "403" if expected_denied else "not 403"
        assert denied is expected_denied, (
            f"{role} {method.upper()} {path}: got {response.status_code}, expected {expectation}"
        )


class TestEgressPolicyChannelIsAdminOnly:
    """Regression: developer could clear a compiled MCPEgressPolicy."""

    def test_developer_cannot_set_l7_policy(self):
        client = _client(role="developer")
        response = client.post("/mcp_servers/srv1/l7_policy", json={"mode": "enforce"})
        assert response.status_code == 403

    def test_developer_cannot_clear_l7_policy(self):
        client = _client(role="developer")
        response = client.delete("/mcp_servers/srv1/l7_policy")
        assert response.status_code == 403

    def test_developer_can_still_write_the_server_itself(self):
        """The narrowing must be surgical: mcp_servers:write is untouched."""
        client = _client(role="developer")
        response = client.delete("/mcp_servers/srv1")
        assert response.status_code != 403


class TestApprovalReadsAreAuthorized:
    """Regression: approval:read was defined, granted, and enforced nowhere."""

    def test_viewer_cannot_list_approvals(self):
        client = _client(role="viewer")
        response = client.get("/approvals")
        assert response.status_code == 403

    def test_auditor_can_list_approvals(self):
        client = _client(role="auditor")
        response = client.get("/approvals")
        assert response.status_code != 403

    def test_viewer_cannot_read_a_single_approval(self):
        client = _client(role="viewer")
        response = client.get("/approvals/a1")
        assert response.status_code == 403


class TestEventStreamIsAuditGrade:
    """/ws/events streams every domain event, so it needs audit:read."""

    def test_viewer_is_denied(self):
        client = _client(role="viewer")
        with pytest.raises(Exception):  # noqa: B017 -- close(1008) surfaces as a connect failure
            with client.websocket_connect("/ws/events"):
                pass


class TestFailClosedDefault:
    """An unmapped route denies instead of reaching its handler."""

    def test_unknown_path_is_denied_for_authenticated_principal(self):
        client = _client(role="admin")
        response = client.get("/definitely-not-mounted")
        assert response.status_code == 403
        assert response.json()["error"] == "access_denied"


class TestAuthenticationStillRequired:
    """No principal means 401, not a permission decision."""

    def test_anonymous_principal_gets_401(self):
        client = _client(role=None)
        response = client.get("/mcp_servers")
        assert response.status_code == 401


class TestMountPrefixStripping:
    """Starlette leaves the mount prefix in scope["path"]; the guard must strip it.

    The served application mounts this router at ``/api``. Starlette >=0.35
    records the consumed prefix in ``root_path`` instead of rewriting ``path``,
    so a guard reading ``path`` raw would look up ``/api/groups`` in a table
    written as ``/groups``, find nothing, and -- because the default is deny --
    reject every REST call. Pinned here as well as in the served-app integration
    test, because this helper is the single point where that goes wrong.
    """

    @staticmethod
    def _relative(path: str, root_path: str) -> str:
        from mcp_hangar.server.api.middleware import AuthorizationEnforcementMiddleware

        return AuthorizationEnforcementMiddleware._router_relative_path(
            {"type": "http", "path": path, "root_path": root_path}
        )

    def test_mount_prefix_is_stripped(self):
        assert self._relative("/api/groups", "/api") == "/groups"
        assert self._relative("/api/mcp_servers/srv1/l7_policy", "/api") == "/mcp_servers/srv1/l7_policy"

    def test_no_root_path_is_a_no_op(self):
        assert self._relative("/groups", "") == "/groups"

    def test_path_equal_to_root_path_becomes_root(self):
        assert self._relative("/api", "/api") == "/"

    def test_non_prefix_root_path_is_left_alone(self):
        """Defensive: never mangle a path that does not start with root_path."""
        assert self._relative("/groups", "/api") == "/groups"


class TestAuthDisabledStaysOpen:
    """#600: arming authorization on an app without authentication bricks it."""

    def test_router_without_auth_components_is_open(self):
        client = TestClient(create_api_router(), raise_server_exceptions=False)
        response = client.get("/system/me")
        assert response.status_code == 200

    def test_router_with_disabled_auth_components_is_open(self):
        disabled = SimpleNamespace(enabled=False, authn_middleware=None, authz_middleware=object())
        client = TestClient(create_api_router(auth_components=disabled), raise_server_exceptions=False)
        response = client.get("/system/me")
        assert response.status_code == 200
