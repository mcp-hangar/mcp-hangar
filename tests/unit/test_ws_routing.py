"""Tests for WebSocket ASGI routing and API router WebSocket mount.

Tests verify:
- combined_app and auth_combined_app route websocket scopes correctly
- /api/ws/* paths are forwarded to api_app with stripped prefix
- Non-/api WebSocket paths fall through to mcp_app
- Existing HTTP routing regressions are caught
- create_api_router includes /ws mount with events and state routes
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.routing import Mount


# ---------------------------------------------------------------------------
# Helpers: build scope dicts
# ---------------------------------------------------------------------------


def _http_scope(path: str) -> dict:
    return {"type": "http", "path": path, "method": "GET", "headers": [], "client": None}


def _ws_scope(path: str) -> dict:
    return {"type": "websocket", "path": path, "headers": [], "client": None}


def _lifespan_scope() -> dict:
    return {"type": "lifespan"}


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# combined_app routing tests
# ---------------------------------------------------------------------------


class TestCombinedAppRouting:
    """Test create_combined_asgi_app websocket + http routing."""

    def _make_combined_app(self):
        from mcp_hangar.fastmcp_server.asgi import create_combined_asgi_app

        aux_app = AsyncMock()
        mcp_app = AsyncMock()
        api_app = AsyncMock()
        app = create_combined_asgi_app(aux_app, mcp_app, api_app)
        return app, aux_app, mcp_app, api_app

    def test_websocket_api_ws_events_goes_to_api_app(self):
        """websocket scope on /api/ws/events is forwarded to api_app."""
        app, aux_app, mcp_app, api_app = self._make_combined_app()
        scope = _ws_scope("/api/ws/events")

        async def run():
            await app(scope, AsyncMock(), AsyncMock())

        _run(run())
        api_app.assert_called_once()
        mcp_app.assert_not_called()

    def test_websocket_api_ws_state_goes_to_api_app(self):
        """websocket scope on /api/ws/state is forwarded to api_app."""
        app, aux_app, mcp_app, api_app = self._make_combined_app()
        scope = _ws_scope("/api/ws/state")

        async def run():
            await app(scope, AsyncMock(), AsyncMock())

        _run(run())
        api_app.assert_called_once()
        mcp_app.assert_not_called()

    def test_websocket_non_api_path_goes_to_mcp_app(self):
        """websocket scope on /mcp is NOT forwarded to api_app."""
        app, aux_app, mcp_app, api_app = self._make_combined_app()
        scope = _ws_scope("/mcp")

        async def run():
            await app(scope, AsyncMock(), AsyncMock())

        _run(run())
        mcp_app.assert_called_once()
        api_app.assert_not_called()

    def test_http_health_still_goes_to_aux_app(self):
        """Regression: http scope on /health still goes to aux_app."""
        app, aux_app, mcp_app, api_app = self._make_combined_app()
        scope = _http_scope("/health")

        async def run():
            await app(scope, AsyncMock(), AsyncMock())

        _run(run())
        aux_app.assert_called_once()
        mcp_app.assert_not_called()
        api_app.assert_not_called()

    def test_http_api_providers_still_goes_to_api_app(self):
        """Regression: http scope on /api/mcp_servers/ still goes to api_app."""
        app, aux_app, mcp_app, api_app = self._make_combined_app()
        scope = _http_scope("/api/mcp_servers/")

        async def run():
            await app(scope, AsyncMock(), AsyncMock())

        _run(run())
        api_app.assert_called_once()
        mcp_app.assert_not_called()

    def test_websocket_api_prefix_stripped_before_forwarding(self):
        """WebSocket scope path has /api prefix stripped: /api/ws/events -> /ws/events."""
        app, aux_app, mcp_app, api_app = self._make_combined_app()
        scope = _ws_scope("/api/ws/events")

        captured_scopes = []

        async def capturing_api_app(scope, receive, send):
            captured_scopes.append(dict(scope))

        from mcp_hangar.fastmcp_server.asgi import create_combined_asgi_app

        app2 = create_combined_asgi_app(aux_app, mcp_app, capturing_api_app)

        async def run():
            await app2(scope, AsyncMock(), AsyncMock())

        _run(run())
        assert len(captured_scopes) == 1
        assert captured_scopes[0]["path"] == "/ws/events"


# ---------------------------------------------------------------------------
# auth_combined_app routing tests
# ---------------------------------------------------------------------------


class TestAuthCombinedAppRouting:
    """Test create_auth_combined_app websocket routing."""

    def _make_auth_app(self):
        from mcp_hangar.fastmcp_server.asgi import create_auth_combined_app

        aux_app = AsyncMock()
        mcp_app = AsyncMock()
        api_app = AsyncMock()

        # Minimal stubs for auth_components and config
        auth_components = MagicMock()
        auth_components.authn_middleware.authenticate.return_value = {"user": "test"}

        config = MagicMock()
        config.auth_skip_paths = ["/health", "/ready", "/metrics"]
        config.trusted_proxies = []

        app = create_auth_combined_app(aux_app, mcp_app, auth_components, config, api_app)
        return app, aux_app, mcp_app, api_app

    def test_auth_app_websocket_api_ws_events_goes_to_api_app(self):
        """auth_combined_app: websocket on /api/ws/events goes to api_app (no auth)."""
        app, aux_app, mcp_app, api_app = self._make_auth_app()
        scope = _ws_scope("/api/ws/events")

        async def run():
            await app(scope, AsyncMock(), AsyncMock())

        _run(run())
        api_app.assert_called_once()
        mcp_app.assert_not_called()

    def test_auth_app_lifespan_falls_through_to_mcp_app(self):
        """auth_combined_app: lifespan scope goes directly to mcp_app."""
        app, aux_app, mcp_app, api_app = self._make_auth_app()
        scope = _lifespan_scope()

        async def run():
            await app(scope, AsyncMock(), AsyncMock())

        _run(run())
        mcp_app.assert_called_once()
        api_app.assert_not_called()


# ---------------------------------------------------------------------------
# router.py WebSocket mount tests
# ---------------------------------------------------------------------------


class TestApiRouterWsMounts:
    """Test create_api_router includes /ws mount with correct routes."""

    def test_router_contains_ws_mount(self):
        """create_api_router returns Starlette app with a /ws Mount."""
        # We need to patch get_context to avoid RuntimeError on import
        with patch("mcp_hangar.server.context.get_context"):
            from mcp_hangar.server.api.router import create_api_router

        router = create_api_router()
        # Extract route paths
        mount_paths = [r.path for r in router.routes]
        assert "/ws" in mount_paths

    def test_ws_mount_contains_events_route(self):
        """The /ws mount contains WebSocketRoute entry for /events."""
        with patch("mcp_hangar.server.context.get_context"):
            from mcp_hangar.server.api.router import create_api_router

        router = create_api_router()
        ws_mount = next(r for r in router.routes if getattr(r, "path", None) == "/ws")
        assert isinstance(ws_mount, Mount)

        ws_route_paths = {r.path for r in ws_mount.routes}
        assert "/events" in ws_route_paths
