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

_IDLE_TIMEOUT_S = 55.0
_PONG_TIMEOUT_S = 10.0


async def ws_events_endpoint(websocket: WebSocket) -> None:
    """Stream domain events to a connected client.

    Protocol:
    1. Client connects; server accepts and immediately subscribes to all events.
    2. Server streams events as JSON objects.
    3. Any client message with {"type": "pong"} is treated as keep-alive response.
    4. After idle timeout, server sends {"type": "ping"} and expects pong.
    5. On disconnect, EventBus handler and connection_manager entry are cleaned up.

    Args:
        websocket: Starlette WebSocket instance.
    """
    await websocket.accept()
    connection_id = str(uuid.uuid4())

    # Subscribe to all events immediately (no filter negotiation).
    # Filters are not used in the current agent protocol.
    filters: dict = {}

    # Shared state for reader/writer coordination.
    pong_received = asyncio.Event()
    client_gone = asyncio.Event()

    async def _reader() -> None:
        """Read incoming client messages (pong responses, filters)."""
        nonlocal filters
        try:
            while True:
                msg = await websocket.receive_json()
                if isinstance(msg, dict):
                    if msg.get("type") == "pong":
                        pong_received.set()
                    elif "event_types" in msg or "provider_ids" in msg:
                        filters = parse_subscription_filters(msg)
        except Exception:  # noqa: BLE001
            pass
        finally:
            client_gone.set()

    reader_task = asyncio.create_task(_reader())

    # Per-connection async queue and event loop capture for thread-safe delivery.
    event_queue = EventStreamQueue()
    loop = asyncio.get_running_loop()

    def event_handler(event) -> None:
        """EventBus callback: runs on any thread that publishes an event."""
        if matches_filters(event, filters):
            event_queue.put_threadsafe(event, loop)

    event_bus = get_event_bus()
    event_bus.subscribe_to_all(event_handler)
    connection_manager.register(connection_id, {"endpoint": "events"})
    logger.info("ws_events_connected", connection_id=connection_id)

    try:
        while not client_gone.is_set():
            try:
                event = await asyncio.wait_for(
                    event_queue.queue.get(),
                    timeout=_IDLE_TIMEOUT_S,
                )
                payload = json.dumps(event.to_dict(), cls=HangarJSONEncoder)
                await websocket.send_text(payload)
            except TimeoutError:
                if client_gone.is_set():
                    break
                try:
                    await websocket.send_json({"type": "ping"})
                except (RuntimeError, WebSocketDisconnect):
                    break
                pong_received.clear()
                try:
                    await asyncio.wait_for(pong_received.wait(), timeout=_PONG_TIMEOUT_S)
                except TimeoutError:
                    logger.debug("ws_events_pong_timeout", connection_id=connection_id)
                    break
            except (RuntimeError, WebSocketDisconnect):
                break
    except WebSocketDisconnect:
        logger.debug("ws_events_disconnected", connection_id=connection_id)
    finally:
        reader_task.cancel()
        event_bus.unsubscribe_from_all(event_handler)
        connection_manager.unregister(connection_id)
        logger.info("ws_events_cleanup_done", connection_id=connection_id)
