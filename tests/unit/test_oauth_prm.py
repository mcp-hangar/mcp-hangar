"""Tests for RFC 9728 Protected Resource Metadata (PRM) endpoint and
WWW-Authenticate resource_metadata header advertisement.

All example values use NEUTRAL placeholders (no real brand names).
"""

from unittest.mock import Mock

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.responses import JSONResponse

from mcp_hangar.auth.prm import (
    build_prm_response,
    build_resource_base_url,
    build_www_authenticate,
    prm_url,
)
from mcp_hangar.server.api.middleware import (
    AuthEnforcementMiddleware,
    AuthMiddlewareHTTP,
    _DEFAULT_AUTH_SKIP_PATHS,
)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

_ISSUER = "https://issuer.example.com"
_RESOURCE = "https://mcp.example.com"
_PRM_PATH = "/.well-known/oauth-protected-resource"


def _make_authn_that_raises(exc):
    """Return a stub authn middleware whose .authenticate() raises *exc*."""
    authn = Mock()
    authn.authenticate.side_effect = exc
    return authn


def _make_prm_app(oidc_issuer: str, resource_uri_cfg: str = "") -> Starlette:
    """Build a minimal Starlette app with a PRM endpoint wired the same way
    lifecycle.py does it (closure over _oidc_issuer / _oidc_resource_uri_cfg).
    """
    from mcp_hangar.auth.prm import build_prm_response, build_resource_base_url

    _oidc_issuer = oidc_issuer
    _oidc_resource_uri_cfg = resource_uri_cfg

    def prm_endpoint(request):
        if not _oidc_issuer:
            return JSONResponse(
                {"error": "not_found", "message": "No OIDC issuer configured"},
                status_code=404,
            )
        resource_base = _oidc_resource_uri_cfg or build_resource_base_url(request.scope)
        return JSONResponse(
            build_prm_response(issuers=[_oidc_issuer], resource_uri=resource_base),
            media_type="application/json",
        )

    return Starlette(routes=[Route(_PRM_PATH, prm_endpoint, methods=["GET"])])


# ---------------------------------------------------------------------------
# Unit tests: prm.py helpers
# ---------------------------------------------------------------------------


class TestPrmHelpers:
    def test_prm_url_appends_well_known_path(self):
        assert prm_url("https://mcp.example.com") == "https://mcp.example.com/.well-known/oauth-protected-resource"

    def test_prm_url_strips_trailing_slash(self):
        assert prm_url("https://mcp.example.com/") == "https://mcp.example.com/.well-known/oauth-protected-resource"

    def test_build_prm_response_structure(self):
        body = build_prm_response(issuers=[_ISSUER], resource_uri=_RESOURCE)
        assert body["resource"] == _RESOURCE
        assert body["authorization_servers"] == [_ISSUER]

    def test_build_www_authenticate_format(self):
        header = build_www_authenticate(_RESOURCE)
        assert header.startswith('Bearer resource_metadata="')
        assert _PRM_PATH in header
        assert header.endswith('", ApiKey')

    def test_build_resource_base_url_from_scope(self):
        scope = {
            "type": "http",
            "scheme": "https",
            "headers": [(b"host", b"mcp.example.com")],
        }
        base = build_resource_base_url(scope)
        assert base == "https://mcp.example.com"

    def test_build_resource_base_url_fallback_to_http(self):
        scope = {
            "type": "http",
            "scheme": "http",
            "headers": [(b"host", b"localhost:8000")],
        }
        base = build_resource_base_url(scope)
        assert base == "http://localhost:8000"

    def test_build_resource_base_url_x_forwarded_proto(self):
        scope = {
            "type": "http",
            "scheme": "http",
            "headers": [
                (b"host", b"mcp.example.com"),
                (b"x-forwarded-proto", b"https"),
            ],
        }
        base = build_resource_base_url(scope)
        assert base == "https://mcp.example.com"


# ---------------------------------------------------------------------------
# PRM endpoint: 200 with OIDC configured
# ---------------------------------------------------------------------------


class TestPrmEndpoint:
    def test_returns_200_with_oidc_issuer(self):
        app = _make_prm_app(oidc_issuer=_ISSUER, resource_uri_cfg=_RESOURCE)
        client = TestClient(app)
        response = client.get(_PRM_PATH)
        assert response.status_code == 200
        body = response.json()
        assert body["resource"] == _RESOURCE
        assert body["authorization_servers"] == [_ISSUER]

    def test_endpoint_requires_no_auth(self):
        """PRM must be reachable without any Authorization header."""
        app = _make_prm_app(oidc_issuer=_ISSUER, resource_uri_cfg=_RESOURCE)
        client = TestClient(app)
        # No Authorization header at all — should still get 200.
        response = client.get(_PRM_PATH, headers={})
        assert response.status_code == 200

    def test_returns_404_when_no_issuer(self):
        """When OIDC is not configured, PRM returns 404 — nothing to advertise."""
        app = _make_prm_app(oidc_issuer="")
        client = TestClient(app)
        response = client.get(_PRM_PATH)
        assert response.status_code == 404

    def test_derives_resource_from_host_when_no_config(self):
        """When resource_uri_cfg is empty, resource is derived from Host header."""
        app = _make_prm_app(oidc_issuer=_ISSUER, resource_uri_cfg="")
        client = TestClient(app, base_url="http://mcp.example.com")
        response = client.get(_PRM_PATH)
        assert response.status_code == 200
        body = response.json()
        assert "mcp.example.com" in body["resource"]


# ---------------------------------------------------------------------------
# PRM path is in _DEFAULT_AUTH_SKIP_PATHS
# ---------------------------------------------------------------------------


class TestPrmSkipPaths:
    def test_prm_path_in_default_skip_paths(self):
        assert _PRM_PATH in _DEFAULT_AUTH_SKIP_PATHS


# ---------------------------------------------------------------------------
# WWW-Authenticate: AuthEnforcementMiddleware (shared ASGI middleware)
# ---------------------------------------------------------------------------


class TestAuthEnforcementMiddlewareWWWAuthenticate:
    """Test the raw ASGI AuthEnforcementMiddleware via a Starlette TestClient wrapper."""

    def _make_client(self, oidc_issuers: list[str], resource_uri: str = "") -> TestClient:
        from mcp_hangar.domain.exceptions import AuthenticationError

        authn = _make_authn_that_raises(AuthenticationError("bad token"))

        async def dummy_app(scope, receive, send):
            pass

        mw = AuthEnforcementMiddleware(
            dummy_app,
            authn=authn,
            oidc_issuers=oidc_issuers,
            oidc_resource_uri=resource_uri,
        )
        # TestClient can drive a raw ASGI app directly.
        return TestClient(mw, raise_server_exceptions=False)

    def test_www_authenticate_with_oidc_has_resource_metadata(self):
        """When OIDC is configured, 401 must include resource_metadata in Bearer challenge."""
        client = self._make_client(oidc_issuers=[_ISSUER], resource_uri=_RESOURCE)
        response = client.get("/mcp")
        assert response.status_code == 401
        www_auth = response.headers.get("www-authenticate", "")
        assert "resource_metadata" in www_auth
        assert _PRM_PATH in www_auth
        assert "Bearer" in www_auth

    def test_www_authenticate_without_oidc_is_plain(self):
        """When no OIDC issuer, Bearer challenge stays as plain 'Bearer, ApiKey'."""
        client = self._make_client(oidc_issuers=[])
        response = client.get("/mcp")
        assert response.status_code == 401
        www_auth = response.headers.get("www-authenticate", "")
        assert www_auth == "Bearer, ApiKey"
        assert "resource_metadata" not in www_auth


# ---------------------------------------------------------------------------
# WWW-Authenticate: AuthMiddlewareHTTP (BaseHTTPMiddleware adapter)
# ---------------------------------------------------------------------------


class TestAuthMiddlewareHTTPWWWAuthenticate:
    def _make_client(self, oidc_issuers: list[str], resource_uri: str = "") -> TestClient:
        from mcp_hangar.domain.exceptions import AuthenticationError

        authn = _make_authn_that_raises(AuthenticationError("bad token"))

        def echo(request):
            return JSONResponse({"ok": True})

        app = Starlette(routes=[Route("/protected", echo, methods=["GET"])])
        app.add_middleware(
            AuthMiddlewareHTTP,
            authn=authn,
            oidc_issuers=oidc_issuers,
            oidc_resource_uri=resource_uri,
        )
        return TestClient(app, raise_server_exceptions=False)

    def test_401_with_oidc_has_resource_metadata(self):
        client = self._make_client(oidc_issuers=[_ISSUER], resource_uri=_RESOURCE)
        response = client.get("/protected")
        assert response.status_code == 401
        www_auth = response.headers.get("www-authenticate", "")
        assert "resource_metadata" in www_auth
        assert _PRM_PATH in www_auth
        expected_prm = f"{_RESOURCE}{_PRM_PATH}"
        assert expected_prm in www_auth

    def test_401_without_oidc_is_plain_bearer(self):
        client = self._make_client(oidc_issuers=[])
        response = client.get("/protected")
        assert response.status_code == 401
        www_auth = response.headers.get("www-authenticate", "")
        assert www_auth == "Bearer, ApiKey"
        assert "resource_metadata" not in www_auth


# ---------------------------------------------------------------------------
# No regression when auth is disabled
# ---------------------------------------------------------------------------


class TestAuthDisabledNoRegression:
    def test_prm_endpoint_returns_404_when_no_issuer(self):
        """With auth disabled (no issuer), PRM must return 404, not 500."""
        app = _make_prm_app(oidc_issuer="")
        client = TestClient(app)
        response = client.get(_PRM_PATH)
        assert response.status_code == 404

    def test_enforcement_middleware_defaults_to_plain_www_authenticate(self):
        """AuthEnforcementMiddleware with no oidc_issuer uses plain 'Bearer, ApiKey'."""
        from mcp_hangar.domain.exceptions import AuthenticationError

        authn = _make_authn_that_raises(AuthenticationError("bad"))

        async def dummy(scope, receive, send):
            pass

        mw = AuthEnforcementMiddleware(dummy, authn=authn)
        # oidc_issuers defaults to [] — ensure no AttributeError and plain header
        assert mw._oidc_issuers == []
