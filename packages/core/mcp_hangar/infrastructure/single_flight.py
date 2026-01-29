"""Single-flight pattern implementation.

Ensures a function is only executed once for a given key, even with concurrent callers.
Subsequent callers wait for the first execution to complete and share the result.

This is useful for:
- Provider cold starts: multiple batch calls to the same COLD provider should trigger
  only one startup, with other callers waiting for completion.
- Expensive computations: avoid duplicate work when multiple requests need the same result.

Thread-safe implementation using threading primitives.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
import threading
from typing import Any, TypeVar

from ..logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass
class _CallState:
    """State for an in-flight call."""

    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    exception: Exception | None = None
    completed: bool = False


class SingleFlight:
    """Ensures a function is only executed once for a given key.

    Thread-safe implementation that allows multiple callers to request
    the same computation, but only one actually executes it.

    Example:
        single_flight = SingleFlight()

        def expensive_operation():
            time.sleep(5)
            return "result"

        # These two calls happen concurrently from different threads:
        # Thread 1:
        result1 = single_flight.do("key1", expensive_operation)  # Executes

        # Thread 2 (called while Thread 1 is executing):
        result2 = single_flight.do("key1", expensive_operation)  # Waits, gets same result

        # result1 == result2, but expensive_operation only ran once
    """

    def __init__(self, cache_results: bool = False):
        """Initialize SingleFlight.

        Args:
            cache_results: If True, results are cached permanently (useful for cold starts).
                          If False, only in-flight deduplication (no caching after completion).
        """
        self._lock = threading.Lock()
        self._calls: dict[str, _CallState] = {}
        self._cache_results = cache_results

    def do(self, key: str, fn: Callable[[], T]) -> T:
        """Execute function for key, or wait for in-flight execution.

        If another caller is currently executing fn for the same key,
        this call will block until that execution completes and return
        the same result (or raise the same exception).

        Args:
            key: Unique identifier for this computation.
            fn: Zero-argument callable to execute.

        Returns:
            Result of fn() execution.

        Raises:
            Any exception raised by fn().
        """
        with self._lock:
            # Check if we have a cached result
            if key in self._calls:
                state = self._calls[key]
                if state.completed:
                    if self._cache_results:
                        # Return cached result
                        if state.exception:
                            raise state.exception
                        return state.result
                    else:
                        # Clear completed state, allow new execution
                        del self._calls[key]
                else:
                    # In-flight - wait for completion
                    pass
            else:
                state = None

            if state is None or state.completed:
                # We are the first caller - create new state and execute
                state = _CallState()
                self._calls[key] = state
                execute = True
            else:
                # Another caller is executing - we'll wait
                execute = False

        if not execute:
            # Wait for the executing thread to complete
            logger.debug("single_flight_waiting", key=key)
            state.event.wait()

            if state.exception:
                raise state.exception
            return state.result

        # We are the executor
        logger.debug("single_flight_executing", key=key)
        try:
            result = fn()
            state.result = result
            state.exception = None
            logger.debug("single_flight_completed", key=key)
            return result
        except Exception as e:
            state.exception = e
            logger.debug("single_flight_failed", key=key, error=str(e))
            raise
        finally:
            state.completed = True
            state.event.set()

            # Clean up if not caching
            if not self._cache_results:
                with self._lock:
                    if key in self._calls and self._calls[key] is state:
                        del self._calls[key]

    def forget(self, key: str) -> bool:
        """Remove a cached result for a key.

        Only useful when cache_results=True.

        Args:
            key: Key to forget.

        Returns:
            True if key was found and removed, False otherwise.
        """
        with self._lock:
            if key in self._calls:
                del self._calls[key]
                return True
            return False

    def clear(self) -> None:
        """Clear all cached results.

        Only affects completed calls. In-flight calls continue normally.
        """
        with self._lock:
            # Only remove completed calls
            keys_to_remove = [k for k, v in self._calls.items() if v.completed]
            for key in keys_to_remove:
                del self._calls[key]
