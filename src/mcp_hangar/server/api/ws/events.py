"""WebSocket endpoint for real-time domain event streaming."""

import asyncio
import json
import uuid
from typing import cast

from ....domain.events import DomainEvent
from starlette.websockets import WebSocket, WebSocketDisconnect

from ....infrastructure.event_bus import get_event_bus
from ....logging_config import get_logger
from ..middleware import get_cors_config
from ..serializers import HangarJSONEncoder
from .filters import matches_filters, parse_subscription_filters
from .manager import EventStreamQueue, connection_manager

logger = get_logger(__name__)

_IDLE_TIMEOUT_S = 55.0
_PONG_TIMEOUT_S = 10.0
_SUBSCRIBE_TIMEOUT_S = 5.0


def _origin_allowed(websocket: WebSocket) -> bool:
    """Return whether the websocket Origin header is allowed."""
    origin = websocket.headers.get("origin")
    if not origin:
        return True

    allowed_origins = set(cast(list[str], get_cors_config()["allow_origins"]))
    return origin in allowed_origins


async def ws_events_endpoint(websocket: WebSocket) -> None:
    """Stream domain events to a connected client.

    Protocol:
    1. Client connects; server accepts and waits briefly for an optional subscribe message.
    2. Server streams events as JSON objects.
    3. Any client message with {"type": "pong"} is treated as keep-alive response.
    4. After idle timeout, server sends {"type": "ping"} and expects pong.
    5. On disconnect, EventBus handler and connection_manager entry are cleaned up.

    Args:
        websocket: Starlette WebSocket instance.
    """
    if not _origin_allowed(websocket):
        logger.warning(
            "ws_origin_rejected",
            origin=websocket.headers.get("origin"),
            path=websocket.url.path,
        )
        await websocket.close(code=1008)
        return

    await websocket.accept()
    connection_id = str(uuid.uuid4())

    # Default to no filtering for backward compatibility when negotiation is absent.
    filters: dict[str, list[str]] = {}

    # Shared state for reader/writer coordination.
    pong_received = asyncio.Event()
    client_gone = asyncio.Event()

    async def _handle_client_message(msg: object, *, acknowledge_subscribe: bool = False) -> None:
        """Process a client control message."""
        nonlocal filters
        if not isinstance(msg, dict):
            return

        typed_msg = cast(dict[str, object], msg)

        if typed_msg.get("type") == "pong":
            pong_received.set()
            return

        if typed_msg.get("type") == "subscribe" or "event_types" in typed_msg or "provider_ids" in typed_msg:
            filters = parse_subscription_filters(typed_msg)
            if acknowledge_subscribe and typed_msg.get("type") == "subscribe":
                await websocket.send_json(
                    {
                        "type": "subscribed",
                        "event_types": filters.get("event_types", []),
                        "provider_ids": filters.get("provider_ids", []),
                    }
                )

    try:
        initial_msg = cast(object, await asyncio.wait_for(websocket.receive_json(), timeout=_SUBSCRIBE_TIMEOUT_S))
    except TimeoutError:
        initial_msg = None
    except (RuntimeError, WebSocketDisconnect):
        return
    else:
        await _handle_client_message(initial_msg, acknowledge_subscribe=True)

    async def _reader() -> None:
        """Read incoming client messages (pong responses, filters)."""
        try:
            while True:
                msg = cast(object, await websocket.receive_json())
                await _handle_client_message(msg)
        except Exception:  # noqa: BLE001
            pass
        finally:
            client_gone.set()

    reader_task = asyncio.create_task(_reader())

    # Per-connection async queue and event loop capture for thread-safe delivery.
    def _on_drop(dropped_event: object, new_event: object) -> None:
        logger.warning(
            "ws_event_dropped",
            connection_id=connection_id,
            dropped_event_type=type(dropped_event).__name__,
            new_event_type=type(new_event).__name__,
        )

    event_queue = EventStreamQueue(maxsize=1024, on_drop=_on_drop)
    loop = asyncio.get_running_loop()

    def event_handler(event: DomainEvent) -> None:
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
                event = cast(DomainEvent, await asyncio.wait_for(event_queue.queue.get(), timeout=_IDLE_TIMEOUT_S))
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
                    _ = await asyncio.wait_for(pong_received.wait(), timeout=_PONG_TIMEOUT_S)
                except TimeoutError:
                    logger.debug("ws_events_pong_timeout", connection_id=connection_id)
                    break
            except (RuntimeError, WebSocketDisconnect):
                break
    except WebSocketDisconnect:
        logger.debug("ws_events_disconnected", connection_id=connection_id)
    finally:
        _ = reader_task.cancel()
        event_bus.unsubscribe_from_all(event_handler)
        connection_manager.unregister(connection_id)
        logger.info("ws_events_cleanup_done", connection_id=connection_id)
