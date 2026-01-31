"""Query classes for CQRS read operations.

Query classes represent requests for data without side effects.
They are immutable and should be named as questions (GetProvider, ListProviders).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Query(ABC):
    """Base class for all queries.

    Queries are immutable and represent a request for data.
    They should be named as questions (GetProvider, ListProviders).
    """

    pass


class QueryHandler(ABC):
    """Base class for query handlers."""

    @abstractmethod
    def handle(self, query: Query) -> Any:
        """Handle the query and return result."""
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
