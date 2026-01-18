"""
Query Bus - dispatches queries to their handlers.

Queries represent requests for data without side effects.
Each query has exactly one handler that returns data.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Query(ABC):
    """Base class for all queries.

    Queries are immutable and represent a request for data.
    They should be named as questions (GetProvider, ListProviders).
    """

    pass


@dataclass(frozen=True)
class ListProvidersQuery(Query):
    """Query to list all providers."""

    state_filter: str | None = None  # Filter by state (cold, ready, degraded, etc.)


@dataclass(frozen=True)
class GetProviderQuery(Query):
    """Query to get a specific provider's details."""

    provider_id: str


@dataclass(frozen=True)
class GetProviderToolsQuery(Query):
    """Query to get tools for a specific provider."""

    provider_id: str


@dataclass(frozen=True)
class GetProviderHealthQuery(Query):
    """Query to get health status of a provider."""

    provider_id: str


@dataclass(frozen=True)
class GetSystemMetricsQuery(Query):
    """Query to get overall system metrics."""

    pass


class QueryHandler(ABC):
    """Base class for query handlers."""

    @abstractmethod
    def handle(self, query: Query) -> Any:
        """Handle the query and return result."""
        pass


class QueryBus:
    """
    Dispatches queries to their registered handlers.

    Each query type can have exactly one handler.
    Queries are read-only and should not modify state.
    """

    def __init__(self):
        self._handlers: dict[type[Query], QueryHandler] = {}

    def register(self, query_type: type[Query], handler: QueryHandler) -> None:
        """
        Register a handler for a query type.

        Args:
            query_type: The type of query to handle
            handler: The handler instance

        Raises:
            ValueError: If a handler is already registered for this query type
        """
        if query_type in self._handlers:
            raise ValueError(f"Handler already registered for {query_type.__name__}")
        self._handlers[query_type] = handler
        logger.debug("query_handler_registered", query_type=query_type.__name__)

    def unregister(self, query_type: type[Query]) -> bool:
        """
        Unregister a handler for a query type.

        Returns:
            True if handler was removed, False if not found
        """
        if query_type in self._handlers:
            del self._handlers[query_type]
            return True
        return False

    def execute(self, query: Query) -> Any:
        """
        Execute a query and return the result.

        Args:
            query: The query to execute

        Returns:
            The result from the handler

        Raises:
            ValueError: If no handler is registered for this query type
        """
        query_type = type(query)
        handler = self._handlers.get(query_type)

        if handler is None:
            raise ValueError(f"No handler registered for {query_type.__name__}")

        logger.debug("query_executing", query_type=query_type.__name__)
        return handler.handle(query)

    def has_handler(self, query_type: type[Query]) -> bool:
        """Check if a handler is registered for the query type."""
        return query_type in self._handlers


# Global query bus instance
_query_bus: QueryBus | None = None


def get_query_bus() -> QueryBus:
    """Get the global query bus instance."""
    global _query_bus
    if _query_bus is None:
        _query_bus = QueryBus()
    return _query_bus


def reset_query_bus() -> None:
    """Reset the global query bus (for testing)."""
    global _query_bus
    _query_bus = None
