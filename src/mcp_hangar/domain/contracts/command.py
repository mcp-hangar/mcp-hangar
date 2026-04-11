"""Command handler contract - interface for command handlers.

Defines the CommandHandler ABC that all command handlers must implement.
Placed in domain/contracts so application layer can import it without
depending on infrastructure.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..commands import Command


class CommandHandler(ABC):
    """Base class for command handlers.

    All concrete command handlers must implement the handle() method.
    Handlers receive a single command and return a result (or raise on error).
    """

    @abstractmethod
    def handle(self, command: "Command") -> Any:
        """Handle the command and return result.

        Args:
            command: The command to handle.

        Returns:
            Result of the command execution (handler-specific).

        Raises:
            Domain exceptions on business rule violations.
        """
