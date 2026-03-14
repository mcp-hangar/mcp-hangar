"""WebSocket endpoint package.

Exports ws_routes for mounting in the API router.
"""

from starlette.routing import WebSocketRoute

from .events import ws_events_endpoint
from .state import ws_state_endpoint

ws_routes = [
    WebSocketRoute("/events", ws_events_endpoint),
    WebSocketRoute("/state", ws_state_endpoint),
]

__all__ = ["ws_routes"]
