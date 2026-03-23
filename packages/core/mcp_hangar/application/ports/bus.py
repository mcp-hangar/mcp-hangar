"""Bus ports - protocols for command and query buses.

Defines ICommandBus and IQueryBus so application/commands and
application/queries can accept bus arguments typed to interfaces
rather than concrete infrastructure classes.
"""

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..commands import Command


class ICommandBus(ABC):
    """Interface for the command bus.

    register_all_handlers functions accept ICommandBus so they do not
    depend on infrastructure.CommandBus directly.
    """

    @abstractmethod
    def register(self, command_type: type, handler: Any) -> None:
        """Register a handler for a command type.

        Args:
            command_type: The type of command to handle.
            handler: The handler instance (must implement handle()).
        """

    @abstractmethod
    def send(self, command: "Command") -> Any:
        """Dispatch a command to its registered handler.

        Args:
            command: The command to dispatch.

        Returns:
            Result from the handler.
        """


class IQueryBus(ABC):
    """Interface for the query bus.

    register_all_handlers functions accept IQueryBus so they do not
    depend on infrastructure.QueryBus directly.
    """

    @abstractmethod
    def register(self, query_type: type, handler: Any) -> None:
        """Register a handler for a query type.

        Args:
            query_type: The type of query to handle.
            handler: The handler instance (must implement handle()).
        """
