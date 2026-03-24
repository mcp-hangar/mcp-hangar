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
