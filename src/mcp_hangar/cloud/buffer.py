"""Bounded in-memory event buffer.

Collects domain events between batch flushes. When full, drops oldest
events (FIFO eviction) and logs a warning.
"""

from collections import deque
import threading
from typing import Any

from ..logging_config import get_logger

logger = get_logger(__name__)


class EventBuffer:
    """Thread-safe bounded buffer for cloud-bound events."""

    def __init__(self, max_size: int = 10_000) -> None:
        self._buf: deque[dict[str, Any]] = deque(maxlen=max_size)
        self._lock = threading.Lock()
        self._dropped: int = 0

    def push(self, event: dict[str, Any]) -> None:
        with self._lock:
            if len(self._buf) == self._buf.maxlen:
                self._dropped += 1
                if self._dropped % 100 == 1:
                    logger.warning("cloud_buffer_overflow", dropped_total=self._dropped)
            self._buf.append(event)

    def drain(self, max_items: int = 500) -> list[dict[str, Any]]:
        """Remove and return up to *max_items* events."""
        with self._lock:
            n = min(max_items, len(self._buf))
            batch = [self._buf.popleft() for _ in range(n)]
        return batch

    @property
    def size(self) -> int:
        return len(self._buf)

    @property
    def dropped(self) -> int:
        return self._dropped
