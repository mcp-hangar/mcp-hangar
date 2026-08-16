"""Tests for WebSocket ASGI routing and API router WebSocket mount.

Tests verify:
- combined_app and auth_combined_app route websocket scopes correctly
- /api/ws/* paths are forwarded to api_app with stripped prefix
- Non-/api WebSocket paths fall through to mcp_app
- Existing HTTP routing regressions are caught
- create_api_router includes /ws mount with events and state routes
"""

from unittest.mock import patch

from starlette.routing import Mount

# ---------------------------------------------------------------------------
# Helpers: build scope dicts
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# combined_app routing tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# auth_combined_app routing tests
# ---------------------------------------------------------------------------


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
