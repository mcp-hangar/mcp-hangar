"""ProviderLogBuffer -- ring-buffer implementation of IProviderLogBuffer.

Also provides a module-level singleton registry so launchers can look up
or create the buffer for a given provider without passing instances around.
"""

import threading
from collections import deque

from ...domain.contracts.log_buffer import IProviderLogBuffer
from ...domain.value_objects.log import LogLine

# ---------------------------------------------------------------------------
# Default capacity
# ---------------------------------------------------------------------------

DEFAULT_MAX_LINES: int = 1000


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


class ProviderLogBuffer(IProviderLogBuffer):
    """Thread-safe ring buffer that stores the most recent log lines.

    Uses a :class:`collections.deque` with a fixed ``maxlen`` so that appending
    beyond capacity silently discards the oldest entry -- O(1) append, O(k) tail.

    Args:
        provider_id: Identifier of the provider this buffer belongs to.
        max_lines: Maximum number of lines to retain.  Defaults to
            :data:`DEFAULT_MAX_LINES` (1000).
    """

    def __init__(self, provider_id: str, max_lines: int = DEFAULT_MAX_LINES) -> None:
        self._provider_id = provider_id
        self._max_lines = max_lines
        self._buffer: deque[LogLine] = deque(maxlen=max_lines)
        self._lock = threading.Lock()

    @property
    def provider_id(self) -> str:
        """Identifier of the provider this buffer belongs to."""
        return self._provider_id

    def append(self, line: LogLine) -> None:
        """Add a log line to the buffer.

        When the buffer is full the oldest line is automatically discarded.

        Args:
            line: The :class:`~mcp_hangar.domain.value_objects.log.LogLine` to store.
        """
        with self._lock:
            self._buffer.append(line)

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

_registry: dict[str, IProviderLogBuffer] = {}
_registry_lock = threading.Lock()


def get_log_buffer(provider_id: str) -> IProviderLogBuffer | None:
    """Return the registered :class:`IProviderLogBuffer` for *provider_id*.

    Returns ``None`` if no buffer has been registered for that provider.

    Args:
        provider_id: Provider identifier to look up.

    Returns:
        The registered buffer, or ``None``.
    """
    with _registry_lock:
        return _registry.get(provider_id)


def get_or_create_log_buffer(provider_id: str, max_lines: int = DEFAULT_MAX_LINES) -> IProviderLogBuffer:
    """Return the registered buffer for *provider_id*, creating one if absent.

    Idempotent -- safe to call from multiple threads; only one buffer is ever
    created per provider.

    Args:
        provider_id: Provider identifier.
        max_lines: Ring-buffer capacity for newly created buffers.

    Returns:
        The existing or freshly created :class:`ProviderLogBuffer`.
    """
    with _registry_lock:
        if provider_id not in _registry:
            _registry[provider_id] = ProviderLogBuffer(provider_id, max_lines=max_lines)
        return _registry[provider_id]


def set_log_buffer(provider_id: str, buffer: IProviderLogBuffer) -> None:
    """Register a custom :class:`IProviderLogBuffer` for *provider_id*.

    Intended for use by the bootstrap layer and tests.  Overwrites any
    previously registered buffer for the same provider.

    Args:
        provider_id: Provider identifier.
        buffer: Buffer instance to register.
    """
    with _registry_lock:
        _registry[provider_id] = buffer


def remove_log_buffer(provider_id: str) -> None:
    """Remove the registered buffer for *provider_id*, if any.

    Args:
        provider_id: Provider identifier.
    """
    with _registry_lock:
        _registry.pop(provider_id, None)


def clear_log_buffer_registry() -> None:
    """Remove all registered buffers.  Primarily useful in tests."""
    with _registry_lock:
        _registry.clear()
