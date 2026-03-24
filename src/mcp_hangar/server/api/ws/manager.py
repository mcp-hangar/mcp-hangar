"""WebSocket connection manager and thread-to-async event queue."""

import asyncio
import threading
from typing import Any

from ....logging_config import get_logger

logger = get_logger(__name__)


class WebSocketConnectionManager:
    """Tracks active WebSocket connections. Thread-safe for concurrent registration.

    A single module-level instance (connection_manager) is used as a singleton.
    """

    def __init__(self) -> None:
        """Initialize connection manager with empty connection registry."""
        self._connections: dict[str, Any] = {}
        self._lock = threading.Lock()

    def register(self, connection_id: str, metadata: Any = None) -> None:
        """Register an active WebSocket connection.

        Args:
            connection_id: Unique identifier for this connection (e.g., UUID string).
            metadata: Optional metadata to store alongside the connection.
        """
        with self._lock:
            self._connections[connection_id] = metadata
        logger.debug("ws_connection_registered", connection_id=connection_id)

    def unregister(self, connection_id: str) -> None:
        """Unregister a WebSocket connection. Silently ignores unknown IDs.

        Args:
            connection_id: The connection ID to remove.
        """
        with self._lock:
            self._connections.pop(connection_id, None)
        logger.debug("ws_connection_unregistered", connection_id=connection_id)

    @property
    def active_count(self) -> int:
        """Number of currently active connections."""
        with self._lock:
            return len(self._connections)


class EventStreamQueue:
    """Per-connection bridge from sync EventBus handlers to async WebSocket send.

    Each WebSocket /events connection creates one EventStreamQueue. The EventBus
    handler (running on whatever thread publishes the event) calls put_threadsafe()
    to schedule delivery onto the connection's asyncio event loop without blocking.

    Slow consumers: events are silently dropped when the queue is full (maxsize=100).
    This prevents a slow/dead client from back-pressuring the EventBus.
    """

    def __init__(self) -> None:
        """Initialize with a bounded asyncio queue (maxsize=100)."""
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=100)

    @property
    def queue(self) -> asyncio.Queue:
        """The underlying asyncio.Queue for use in async WebSocket loops."""
        return self._queue

    def put_threadsafe(self, event: Any, loop: asyncio.AbstractEventLoop) -> None:
        """Schedule event delivery onto the asyncio event loop from any thread.

        Safe to call from any thread, including EventBus handler threads.
        Silently drops the event if the queue is already full.

        Note: call_soon_threadsafe returns immediately -- it does not raise QueueFull.
        The drop must happen inside the scheduled callback. We wrap put_nowait in a
        helper that swallows QueueFull so the error does not surface on stderr.

        Args:
            event: The domain event to deliver.
            loop: The asyncio event loop running the WebSocket handler.
        """
        event_type = type(event).__name__

        def _safe_put() -> None:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("ws_event_queue_full_drop", event_type=event_type)

        loop.call_soon_threadsafe(_safe_put)


# Module-level singleton used by endpoint handlers.
connection_manager = WebSocketConnectionManager()
