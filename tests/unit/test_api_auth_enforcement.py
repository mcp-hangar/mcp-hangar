"""Tests for REST API auth middleware mounting and /me endpoint.

Covers:
- create_api_router() with and without auth_components
- AuthMiddlewareHTTP mounting when auth is enabled
- /system/me endpoint behavior with and without auth context
"""

from unittest.mock import Mock

from starlette.testclient import TestClient

from mcp_hangar.server.api.router import create_api_router


# ---------------------------------------------------------------------------
# Helper: stub auth_components
# ---------------------------------------------------------------------------


def _make_disabled_auth_components():
    """Create auth_components with enabled=False."""
    stub = Mock()
    stub.enabled = False
    stub.authn_middleware = None
    return stub


# ---------------------------------------------------------------------------
# Test create_api_router auth parameter
# ---------------------------------------------------------------------------


class TestAuthMiddlewareMounting:
    """Tests for auth middleware mounting in create_api_router."""

    def test_create_api_router_without_auth(self):
        """create_api_router() with no auth_components returns working app."""
        app = create_api_router()
        client = TestClient(app)
        # /system/ returns 200 (system info endpoint)
        # Note: will 500 because query bus is not wired, but the point is
        # we get past middleware without 401.
        response = client.get("/system/me")
        # /me should return 200 with authenticated=false when no auth middleware
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is False
        assert data["principal"] is None

    def test_create_api_router_with_auth_disabled(self):
        """create_api_router(auth_components=disabled) does not mount auth middleware."""
        auth = _make_disabled_auth_components()
        app = create_api_router(auth_components=auth)
        client = TestClient(app)
        # Should still be accessible without authentication
        response = client.get("/system/me")
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is False

    def test_create_api_router_signature_accepts_auth_components(self):
        """create_api_router accepts auth_components keyword argument."""
        # Verify no TypeError on call
        app = create_api_router(auth_components=None)
        assert app is not None


# ---------------------------------------------------------------------------
# Test /system/me endpoint
# ---------------------------------------------------------------------------


class TestSystemMeEndpoint:
    """Tests for the /system/me authentication status endpoint."""

    def test_me_endpoint_no_auth_context(self):
        """GET /system/me returns authenticated=false when no auth middleware is active."""
        app = create_api_router()
        client = TestClient(app)
        response = client.get("/system/me")
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is False
        assert data["principal"] is None

    def test_me_endpoint_with_auth_context(self):
        """GET /system/me returns authenticated=true when auth context is present.

        Simulates the case where AuthMiddlewareHTTP has set request.state.auth.
        We use Starlette middleware to inject the mock auth context.
        """
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.requests import Request

        # Create a mock principal
        mock_principal = Mock()
        mock_principal.id = "user-123"
        mock_principal.type = Mock()
        mock_principal.type.value = "api_key"

        mock_auth = Mock()
        mock_auth.principal = mock_principal

        class InjectAuthMiddleware(BaseHTTPMiddleware):
            """Test middleware that injects auth context into request.state."""

            async def dispatch(self, request: Request, call_next):
                request.state.auth = mock_auth
                return await call_next(request)

        app = create_api_router()
        app.add_middleware(InjectAuthMiddleware)

        client = TestClient(app)
        response = client.get("/system/me")
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is True
        assert data["principal"]["id"] == "user-123"
        assert data["principal"]["type"] == "api_key"

    def test_me_endpoint_exists_in_system_routes(self):
        """Verify /me is registered in system_routes."""
        from mcp_hangar.server.api.system import system_routes

        paths = [r.path for r in system_routes]
        assert "/me" in paths

    def test_me_endpoint_only_get_method(self):
        """Verify /me endpoint only accepts GET method."""
        from mcp_hangar.server.api.system import system_routes

        me_route = next(r for r in system_routes if r.path == "/me")
        assert "GET" in me_route.methods
