"""Async task submitter port.

Defines IAsyncTaskSubmitter so application layer can fire-and-forget
async coroutines without depending on infrastructure.AsyncExecutor directly.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any


class IAsyncTaskSubmitter(ABC):
    """Interface for submitting async coroutines from synchronous context.

    Application event handlers use this to execute async I/O operations
    (e.g., knowledge base writes) without blocking.
    """

    @abstractmethod
    def submit(
        self,
        coro: Coroutine[Any, Any, Any],
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Submit an async coroutine for background execution.

        Fire-and-forget. The coroutine executes in a background thread.

        Args:
            coro: The coroutine to execute.
            on_success: Optional callback on successful completion.
            on_error: Optional callback on error.
        """


class IBlockingAsyncRunner(ABC):
    """Runs a coroutine from synchronous code and **waits** for its result.

    The other half of `IAsyncTaskSubmitter`, and the half that exists because
    fire-and-forget is the wrong answer when the caller needs the value -- or
    needs to know the write happened. A projection reading a configuration row
    cannot carry on without it; a registration cannot report success without
    knowing the row landed.

    A port rather than a direct import, because the implementation is a thread
    and an event loop, which the application layer has no business knowing
    about. The composition root supplies one.
    """

    @abstractmethod
    def run(self, coro: Coroutine[Any, Any, Any], timeout: float) -> Any:
        """Run `coro` elsewhere and return its result.

        Args:
            coro: The coroutine to run.
            timeout: How long to wait before treating it as a failure.

        Returns:
            Whatever the coroutine returned.
        """
