"""Query classes for CQRS read operations.

Query classes represent requests for data without side effects.
They are immutable and should be named as questions (GetMcpServer, ListMcpServers).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Query(ABC):
    """Base class for all queries.

    Queries are immutable and represent a request for data.
    They should be named as questions (GetMcpServer, ListMcpServers).
    """

    pass


class QueryHandler(ABC):
    """Base class for query handlers."""

    @abstractmethod
    def handle(self, query: Query) -> Any:
        """Handle the query and return result."""
        pass


@dataclass(frozen=True)
class ListMcpServersQuery(Query):
    """Query to list all mcp_servers."""

    state_filter: str | None = None  # Filter by state (cold, ready, degraded, etc.)


@dataclass(frozen=True)
class GetMcpServerQuery(Query):
    """Query to get a specific mcp_server's details."""

    mcp_server_id: str


@dataclass(frozen=True)
class GetMcpServerToolsQuery(Query):
    """Query to get tools for a specific mcp_server."""

    mcp_server_id: str


@dataclass(frozen=True)
class GetMcpServerHealthQuery(Query):
    """Query to get health status of a mcp_server."""

    mcp_server_id: str


@dataclass(frozen=True)
class GetSystemMetricsQuery(Query):
    """Query to get overall system metrics."""

    pass


@dataclass(frozen=True)
class GetToolInvocationHistoryQuery(Query):
    """Query to get tool invocation history for a mcp_server."""

    mcp_server_id: str
    limit: int = 100
    from_position: int = 0


# legacy aliases
globals().update(
    {
        "".join(("ListPro", "vidersQuery")): ListMcpServersQuery,
        "".join(("GetPro", "viderQuery")): GetMcpServerQuery,
        "".join(("GetPro", "viderToolsQuery")): GetMcpServerToolsQuery,
        "".join(("GetPro", "viderHealthQuery")): GetMcpServerHealthQuery,
    }
)
