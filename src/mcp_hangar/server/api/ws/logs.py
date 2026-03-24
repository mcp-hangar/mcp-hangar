"""WebSocket endpoint and broadcaster for live provider log streaming (LOG-04).

Architecture:
- ``LogStreamBroadcaster`` -- singleton that bridges sync stderr-reader threads
  to async WebSocket clients via per-provider asyncio.Queue instances.
- ``ws_logs_endpoint`` -- Starlette WebSocket handler that sends buffered history
  on connect, then streams live lines until the client disconnects.

Thread-safety note:
  stderr-reader threads call ``LogStreamBroadcaster.notify()`` from a sync
  context.  The broadcaster uses ``loop.call_soon_threadsafe()`` to deliver
  lines to the asyncio event loop that owns each client queue -- the same
  pattern used by ``EventStreamQueue`` in ``manager.py``.
"""

import asyncio
import threading

from starlette.websockets import WebSocket, WebSocketDisconnect

from ....domain.value_objects.log import LogLine
from ....logging_config import get_logger
from ....infrastructure.persistence.log_buffer import get_log_buffer

logger = get_logger(__name__)

# Maximum lines buffered per client queue before drops start.
_CLIENT_QUEUE_MAXSIZE = 200

# How long to wait for a new line before sending a keepalive ping (seconds).
_IDLE_TIMEOUT_S = 30.0

# How long the client has to respond to a ping (seconds).
_PONG_TIMEOUT_S = 10.0


# ---------------------------------------------------------------------------
# Broadcaster
# ---------------------------------------------------------------------------


class LogStreamBroadcaster:
    """Fan-out bridge: one sync notify() call reaches all async client queues.

    Per-provider dict maps provider_id -> list of (asyncio.Queue, asyncio.AbstractEventLoop)
    pairs.  Each connected WebSocket client owns one queue.

    All public methods are thread-safe -- they may be called from stderr-reader
    daemon threads or from async WebSocket handlers (via ``asyncio.get_event_loop``).
    """

    def __init__(self) -> None:
        """Initialize broadcaster with empty subscriber registry."""
        # provider_id -> list of (queue, loop) tuples for connected clients
        self._subscribers: dict[str, list[tuple[asyncio.Queue, asyncio.AbstractEventLoop]]] = {}
        self._lock = threading.Lock()

    def register(
        self,
        provider_id: str,
        queue: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Register a client queue to receive log lines for *provider_id*.

        Args:
            provider_id: Provider whose log stream the client wants.
            queue: The client's asyncio.Queue -- must be owned by *loop*.
            loop: The asyncio event loop running the WebSocket handler.
        """
        with self._lock:
            if provider_id not in self._subscribers:
                self._subscribers[provider_id] = []
            self._subscribers[provider_id].append((queue, loop))
        logger.debug("log_broadcaster_registered", provider_id=provider_id)

    def unregister(
        self,
        provider_id: str,
        queue: asyncio.Queue,
    ) -> None:
        """Remove a client queue from the subscriber list.

        Silently ignores unknown queues.  Safe to call from async cleanup.

        Args:
            provider_id: Provider whose log stream the client was receiving.
            queue: The queue to remove.
        """
        with self._lock:
            subs = self._subscribers.get(provider_id, [])
            self._subscribers[provider_id] = [(q, lp) for q, lp in subs if q is not queue]
        logger.debug("log_broadcaster_unregistered", provider_id=provider_id)

    def notify(self, line: LogLine) -> None:
        """Deliver *line* to all registered clients for its provider.

        Safe to call from any thread (including stderr-reader daemon threads).
        Uses ``call_soon_threadsafe`` to enqueue onto each client's event loop.
        Slow clients whose queue is full silently lose the line.

        Args:
            line: The log line to broadcast.
        """
        with self._lock:
            subs = list(self._subscribers.get(line.provider_id, []))

        for queue, loop in subs:

            def _safe_put(q=queue, ln=line) -> None:
                try:
                    q.put_nowait(ln)
                except asyncio.QueueFull:
                    logger.debug(
                        "log_broadcaster_queue_full_drop",
                        provider_id=ln.provider_id,
                    )

            loop.call_soon_threadsafe(_safe_put)

    def subscriber_count(self, provider_id: str) -> int:
        """Return the number of active subscribers for *provider_id*.

        Primarily used in tests.

        Args:
            provider_id: Provider to query.

        Returns:
            Number of registered client queues.
        """
        with self._lock:
            return len(self._subscribers.get(provider_id, []))


# Module-level singleton.
log_broadcaster = LogStreamBroadcaster()


def get_log_broadcaster() -> LogStreamBroadcaster:
    """Return the module-level LogStreamBroadcaster singleton.

    Returns:
        The global :class:`LogStreamBroadcaster` instance.
    """
    return log_broadcaster


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


async def ws_logs_endpoint(websocket: WebSocket) -> None:
    """Stream live log lines for a provider to a connected WebSocket client.

    Protocol:
    1. Client connects to ``/api/ws/providers/{provider_id}/logs``.
    2. Server accepts and immediately sends buffered history (last 100 lines)
       as individual JSON messages.
    3. Server then streams live lines as they arrive.
    4. After ``_IDLE_TIMEOUT_S`` seconds with no new line, server sends
       ``{"type": "ping"}`` to detect dead connections.
       Client must respond ``{"type": "pong"}`` within ``_PONG_TIMEOUT_S``
       seconds or the server closes the connection.
    5. On disconnect (clean or error), the client queue is unregistered.

    Each message format::

        {
            "type": "log_line",
            "provider_id": "<provider_id>",
            "stream": "stdout" | "stderr",
            "content": "<line text>",
            "recorded_at": <unix timestamp float>
        }

    Args:
        websocket: Starlette WebSocket instance.
    """
    provider_id: str = websocket.path_params["provider_id"]
    await websocket.accept()
    logger.info("ws_logs_connected", provider_id=provider_id)

    # Send buffered history before registering for live lines to avoid gaps.
    buffer = get_log_buffer(provider_id)
    if buffer is not None:
        try:
            for line in buffer.tail(100):
                await websocket.send_json(_line_to_message(line))
        except (RuntimeError, WebSocketDisconnect):
            logger.debug("ws_logs_disconnected_during_history", provider_id=provider_id)
            return

    # Register for live delivery.
    client_queue: asyncio.Queue[LogLine] = asyncio.Queue(maxsize=_CLIENT_QUEUE_MAXSIZE)
    loop = asyncio.get_event_loop()
    broadcaster = get_log_broadcaster()
    broadcaster.register(provider_id, client_queue, loop)

    try:
        while True:
            try:
                line = await asyncio.wait_for(client_queue.get(), timeout=_IDLE_TIMEOUT_S)
                await websocket.send_json(_line_to_message(line))
            except TimeoutError:
                # No log line for a while -- send ping to check if client is alive.
                try:
                    await websocket.send_json({"type": "ping"})
                except RuntimeError:
                    break
                try:
                    pong = await asyncio.wait_for(
                        websocket.receive_json(),
                        timeout=_PONG_TIMEOUT_S,
                    )
                    if pong.get("type") != "pong":
                        logger.debug("ws_logs_unexpected_message", provider_id=provider_id)
                        break
                except TimeoutError:
                    logger.debug("ws_logs_pong_timeout", provider_id=provider_id)
                    break
            except RuntimeError:
                # Client disconnected while we were about to send -- exit cleanly.
                break
    except WebSocketDisconnect:
        logger.debug("ws_logs_disconnected", provider_id=provider_id)
    finally:
        broadcaster.unregister(provider_id, client_queue)
        logger.info("ws_logs_cleanup_done", provider_id=provider_id)


def _line_to_message(line: LogLine) -> dict:
    """Serialize a LogLine to a WebSocket message dict.

    Args:
        line: The log line to serialize.

    Returns:
        Dict with type, provider_id, stream, content, and recorded_at.
    """
    return {
        "type": "log_line",
        "provider_id": line.provider_id,
        "stream": line.stream,
        "content": line.content,
        "recorded_at": line.recorded_at,
    }
