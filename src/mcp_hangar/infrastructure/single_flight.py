"""Single-flight pattern implementation.

Ensures a function is only executed once for a given key, even with concurrent callers.
Subsequent callers wait for the first execution to complete and share the result.

This is useful for:
- McpServer cold starts: multiple batch calls to the same COLD mcp_server should trigger
  only one startup, with other callers waiting for completion.
- Expensive computations: avoid duplicate work when multiple requests need the same result.

Thread-safe implementation using threading primitives.
"""

import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, cast

from ..logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class SingleFlightObserver(Protocol):
    """Reports which caller executes and which ones wait (#1279).

    Both hooks are called OUTSIDE the lock. Instrumentation inside it would
    hold every other caller for the duration of whatever the observer does,
    which is the opposite of what measuring a wait is for.
    """

    def leading(self, key: str) -> str | None:
        """This caller will execute. Returns an origin token for the waiters.

        The token is opaque data here -- a W3C ``traceparent`` string in the
        tracing adapter (ADR-029 s8) -- so this module never holds an SDK
        object and never has to know what causality means.
        """
        ...

    def waiting(self, key: str, origin: str | None) -> AbstractContextManager[None]:
        """This caller will wait for *origin*'s execution, around the wait."""
        ...


@dataclass
class _CallState:
    """State for an in-flight call."""

    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    exception: Exception | None = None
    completed: bool = False
    #: What the executing caller published for the waiters, if anything. Read
    #: without the lock: a waiter that arrives before the leader publishes gets
    #: None, which the adapter treats as "no link" rather than inventing one.
    origin: str | None = None


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

    def __init__(self, cache_results: bool = False, observer: SingleFlightObserver | None = None):
        """Initialize SingleFlight.

        Args:
            cache_results: If True, results are cached permanently (useful for cold starts).
                          If False, only in-flight deduplication (no caching after completion).
            observer: Optional. Told which caller executes and which ones wait,
                     always outside the lock. None reports nothing.
        """
        self._lock = threading.Lock()
        self._calls: dict[str, _CallState] = {}
        self._cache_results = cache_results
        self._observer = observer

    def _leading(self, key: str) -> str | None:
        """Tell the observer this caller executes, fault-barriered.

        An observer that raises must not turn a cold start into a failed call:
        the point of this hook is to describe the start, not to be able to stop
        it.
        """
        if self._observer is None:
            return None
        try:
            return self._observer.leading(key)
        except Exception:  # noqa: BLE001 -- fault barrier: observation must not break a start
            logger.debug("single_flight_observer_failed", key=key, hook="leading")
            return None

    def _waiting(self, key: str, origin: str | None) -> AbstractContextManager[None]:
        """Tell the observer this caller waits, fault-barriered as above."""
        if self._observer is None:
            return nullcontext()
        try:
            return self._observer.waiting(key, origin)
        except Exception:  # noqa: BLE001 -- fault barrier: observation must not break a wait
            logger.debug("single_flight_observer_failed", key=key, hook="waiting")
            return nullcontext()

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
                        return cast(T, state.result)
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
            with self._waiting(key, state.origin):
                state.event.wait()

            if state.exception:
                raise state.exception
            return cast(T, state.result)

        # We are the executor
        logger.debug("single_flight_executing", key=key)
        state.origin = self._leading(key)
        try:
            result = fn()
            state.result = result
            state.exception = None
            logger.debug("single_flight_completed", key=key)
            return result
        except Exception as e:  # noqa: BLE001 -- infra-boundary: records exception for waiting callers, then re-raises
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
