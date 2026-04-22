"""McpServerLogBuffer -- ring-buffer implementation of IMcpServerLogBuffer.

Also provides a module-level singleton registry so launchers can look up
or create the buffer for a given mcp_server without passing instances around.
"""

import threading
from collections import deque
from collections.abc import Callable

from ...domain.contracts.log_buffer import IMcpServerLogBuffer
from ...domain.value_objects.log import LogLine

# ---------------------------------------------------------------------------
# Default capacity
# ---------------------------------------------------------------------------

DEFAULT_MAX_LINES: int = 1000


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


class McpServerLogBuffer(IMcpServerLogBuffer):
    """Thread-safe ring buffer that stores the most recent log lines.

    Uses a :class:`collections.deque` with a fixed ``maxlen`` so that appending
    beyond capacity silently discards the oldest entry -- O(1) append, O(k) tail.

    An optional *on_append* callback is invoked (synchronously, under no lock)
    after each successful :meth:`append`.  The bootstrap layer uses this to wire
    in :class:`~mcp_hangar.server.api.ws.logs.LogStreamBroadcaster` without
    coupling the domain layer to the WebSocket infrastructure.

    Args:
        mcp_server_id: Identifier of the mcp_server this buffer belongs to.
        max_lines: Maximum number of lines to retain.  Defaults to
            :data:`DEFAULT_MAX_LINES` (1000).
        on_append: Optional callable invoked with each :class:`LogLine` after
            it is stored.  Called from whatever thread calls :meth:`append`.
    """

    def __init__(
        self,
        mcp_server_id: str,
        max_lines: int = DEFAULT_MAX_LINES,
        on_append: Callable[[LogLine], None] | None = None,
    ) -> None:
        self._mcp_server_id = mcp_server_id
        self._max_lines = max_lines
        self._buffer: deque[LogLine] = deque(maxlen=max_lines)
        self._lock = threading.Lock()
        self._on_append = on_append

    @property
    def mcp_server_id(self) -> str:
        """Identifier of the mcp_server this buffer belongs to."""
        return self._mcp_server_id

    def append(self, line: LogLine) -> None:
        """Add a log line to the buffer.

        When the buffer is full the oldest line is automatically discarded.
        If an *on_append* callback was provided at construction time it is
        invoked after the line is stored (outside the lock).

        Args:
            line: The :class:`~mcp_hangar.domain.value_objects.log.LogLine` to store.
        """
        with self._lock:
            self._buffer.append(line)
        if self._on_append is not None:
            self._on_append(line)

    def tail(self, n: int) -> list[LogLine]:
        """Return the most recent *n* log lines, oldest first.

        Args:
            n: Maximum number of lines.

        Returns:
            List of :class:`~mcp_hangar.domain.value_objects.log.LogLine` in
            chronological order (oldest to newest).
        """
        with self._lock:
            items = list(self._buffer)
        if n >= len(items):
            return items
        return items[-n:]

    def clear(self) -> None:
        """Remove all stored log lines."""
        with self._lock:
            self._buffer.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)


# ---------------------------------------------------------------------------
# Singleton registry
# ---------------------------------------------------------------------------

_registry: dict[str, IMcpServerLogBuffer] = {}
_registry_lock = threading.Lock()


def get_log_buffer(mcp_server_id: str) -> IMcpServerLogBuffer | None:
    """Return the registered :class:`IMcpServerLogBuffer` for *mcp_server_id*.

    Returns ``None`` if no buffer has been registered for that mcp_server.

    Args:
        mcp_server_id: McpServer identifier to look up.

    Returns:
        The registered buffer, or ``None``.
    """
    with _registry_lock:
        return _registry.get(mcp_server_id)


def get_or_create_log_buffer(mcp_server_id: str, max_lines: int = DEFAULT_MAX_LINES) -> IMcpServerLogBuffer:
    """Return the registered buffer for *mcp_server_id*, creating one if absent.

    Idempotent -- safe to call from multiple threads; only one buffer is ever
    created per mcp_server.

    Args:
        mcp_server_id: McpServer identifier.
        max_lines: Ring-buffer capacity for newly created buffers.

    Returns:
        The existing or freshly created :class:`McpServerLogBuffer`.
    """
    with _registry_lock:
        if mcp_server_id not in _registry:
            _registry[mcp_server_id] = McpServerLogBuffer(mcp_server_id, max_lines=max_lines)
        return _registry[mcp_server_id]


def set_log_buffer(mcp_server_id: str, buffer: IMcpServerLogBuffer) -> None:
    """Register a custom :class:`IMcpServerLogBuffer` for *mcp_server_id*.

    Intended for use by the bootstrap layer and tests.  Overwrites any
    previously registered buffer for the same mcp_server.

    Args:
        mcp_server_id: McpServer identifier.
        buffer: Buffer instance to register.
    """
    with _registry_lock:
        _registry[mcp_server_id] = buffer


def remove_log_buffer(mcp_server_id: str) -> None:
    """Remove the registered buffer for *mcp_server_id*, if any.

    Args:
        mcp_server_id: McpServer identifier.
    """
    with _registry_lock:
        _registry.pop(mcp_server_id, None)


def clear_log_buffer_registry() -> None:
    """Remove all registered buffers.  Primarily useful in tests."""
    with _registry_lock:
        _registry.clear()


# legacy aliases
ProviderLogBuffer = McpServerLogBuffer
