"""WebSocket endpoint for real-time domain event streaming."""

import asyncio
import json
import uuid

from starlette.websockets import WebSocket, WebSocketDisconnect

from ....infrastructure.event_bus import get_event_bus
from ....logging_config import get_logger
from ..serializers import HangarJSONEncoder
from .filters import matches_filters, parse_subscription_filters
from .manager import EventStreamQueue, connection_manager

logger = get_logger(__name__)

_IDLE_TIMEOUT_S = 30.0
_PONG_TIMEOUT_S = 10.0
_FILTER_TIMEOUT_S = 5.0


async def ws_events_endpoint(websocket: WebSocket) -> None:
    """Stream domain events to a connected client.

    Protocol:
    1. Client connects; server accepts.
    2. Client MAY send optional filter config within 5 seconds:
       {"event_types": [...], "provider_ids": [...]}
       Timeout or absence means no filtering (deliver all events).
    3. Server streams events as JSON objects.
    4. After 30s with no event, server sends {"type": "ping"}.
       Client must respond with {"type": "pong"} within 10s or connection closes.
    5. On disconnect, EventBus handler and connection_manager entry are cleaned up.

    Note: severity filtering is not implemented here -- DomainEvent has no severity field.
    TODO: add severity filter support when DomainEvent gains a severity attribute.

    Args:
        websocket: Starlette WebSocket instance.
    """
    await websocket.accept()
    connection_id = str(uuid.uuid4())

    # Read optional filter config from client (non-blocking, 5s timeout).
    filters: dict = {}
    try:
        filter_msg = await asyncio.wait_for(websocket.receive_json(), timeout=_FILTER_TIMEOUT_S)
        filters = parse_subscription_filters(filter_msg)
    except (TimeoutError, Exception):
        filters = {}

    # Per-connection async queue and event loop capture for thread-safe delivery.
    event_queue = EventStreamQueue()
    loop = asyncio.get_event_loop()

    def event_handler(event) -> None:
        """EventBus callback: runs on any thread that publishes an event."""
        if matches_filters(event, filters):
            event_queue.put_threadsafe(event, loop)

    event_bus = get_event_bus()
    event_bus.subscribe_to_all(event_handler)
    connection_manager.register(connection_id, {"endpoint": "events"})
    logger.info("ws_events_connected", connection_id=connection_id, filters=filters)

    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    event_queue.queue.get(),
                    timeout=_IDLE_TIMEOUT_S,
                )
                await websocket.send_text(json.dumps(event.to_dict(), cls=HangarJSONEncoder))
            except TimeoutError:
                # No event for 30s -- send ping to detect dead connections.
                await websocket.send_json({"type": "ping"})
                try:
                    pong = await asyncio.wait_for(
                        websocket.receive_json(),
                        timeout=_PONG_TIMEOUT_S,
                    )
                    if pong.get("type") != "pong":
                        logger.debug("ws_events_unexpected_message", connection_id=connection_id)
                        break
                except TimeoutError:
                    logger.debug("ws_events_pong_timeout", connection_id=connection_id)
                    break
    except WebSocketDisconnect:
        logger.debug("ws_events_disconnected", connection_id=connection_id)
    finally:
        event_bus.unsubscribe_from_all(event_handler)
        connection_manager.unregister(connection_id)
        logger.info("ws_events_cleanup_done", connection_id=connection_id)
