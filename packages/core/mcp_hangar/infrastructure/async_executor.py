"""Async executor for running async operations from sync context.

This module provides a clean way to execute async coroutines from
synchronous code without blocking, using a background thread pool.

This solves the common problem of "Event loop is closed" errors
when trying to use asyncio from sync handlers.

Usage:
    from mcp_hangar.infrastructure.async_executor import async_executor

    # Fire-and-forget
    async_executor.submit(some_coroutine())

    # With callback
    async_executor.submit(some_coroutine(), on_error=lambda e: logger.error(e))
"""

import asyncio
import atexit
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from ..logging_config import get_logger

logger = get_logger(__name__)


class AsyncExecutor:
    """Executes async coroutines from sync context using a thread pool.

    This class provides a singleton executor that runs async operations
    in background threads, each with their own event loop. This avoids
    the "Event loop is closed" error that occurs when trying to use
    asyncio from sync code.

    Attributes:
        _executor: Thread pool for running async operations
        _max_workers: Maximum number of concurrent background operations
    """

    _instance: Optional["AsyncExecutor"] = None

    def __init__(self, max_workers: int = 4):
        """Initialize the async executor.

        Args:
            max_workers: Maximum number of concurrent background threads
        """
        self._max_workers = max_workers
        self._executor: ThreadPoolExecutor | None = None
        self._started = False

    @classmethod
    def get_instance(cls) -> "AsyncExecutor":
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _ensure_started(self) -> None:
        """Ensure the thread pool is started."""
        if not self._started:
            self._executor = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="async-executor-")
            self._started = True
            # Register cleanup on exit
            atexit.register(self.shutdown)

    def submit(
        self,
        coro: Coroutine[Any, Any, Any],
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Submit an async coroutine for background execution.

        The coroutine will be executed in a background thread with its
        own event loop. This is fire-and-forget by default.

        Args:
            coro: The coroutine to execute
            on_success: Optional callback on successful completion
            on_error: Optional callback on error (default: log debug)
        """
        self._ensure_started()

        def run_coro():
            try:
                result = asyncio.run(coro)
                if on_success:
                    try:
                        on_success(result)
                    except (TypeError, ValueError, RuntimeError) as e:
                        logger.debug("async_executor_callback_error", error=str(e))
            except (TimeoutError, asyncio.CancelledError, ValueError, RuntimeError, OSError) as e:
                # Handle expected async/runtime errors
                if on_error:
                    try:
                        on_error(e)
                    except (TypeError, ValueError, RuntimeError) as callback_err:
                        logger.debug("async_executor_error_callback_failed", error=str(callback_err))
                else:
                    logger.debug("async_executor_error", error_type=type(e).__name__, error=str(e))

        self._executor.submit(run_coro)

    def shutdown(self, wait: bool = False) -> None:
        """Shutdown the executor.

        Args:
            wait: Whether to wait for pending operations to complete
        """
        if self._executor:
            self._executor.shutdown(wait=wait)
            self._executor = None
            self._started = False


# Singleton instance for easy access
async_executor = AsyncExecutor.get_instance()


def submit_async(
    coro: Coroutine[Any, Any, Any],
    on_error: Callable[[Exception], None] | None = None,
) -> None:
    """Convenience function to submit an async operation.

    Args:
        coro: The coroutine to execute
        on_error: Optional error callback
    """
    async_executor.submit(coro, on_error=on_error)
