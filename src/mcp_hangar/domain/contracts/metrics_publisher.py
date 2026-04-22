"""Metrics Publisher contract for domain layer.

This interface allows the domain to publish metrics without depending
on concrete metrics implementation (Prometheus, statsd, etc.).
"""

from abc import ABC, abstractmethod


class IMetricsPublisher(ABC):
    """Contract for publishing metrics from domain layer."""

    @abstractmethod
    def record_cold_start(self, mcp_server_id: str, duration_s: float, mode: str) -> None:
        """
        Record a cold start event.

        Args:
            mcp_server_id: McpServer identifier
            duration_s: Duration of cold start in seconds
            mode: McpServer mode (subprocess, docker, etc.)
        """
        pass

    @abstractmethod
    def begin_cold_start(self, mcp_server_id: str) -> None:
        """
        Mark the beginning of a cold start.

        Args:
            mcp_server_id: McpServer identifier
        """
        pass

    @abstractmethod
    def end_cold_start(self, mcp_server_id: str) -> None:
        """
        Mark the end of a cold start.

        Args:
            mcp_server_id: McpServer identifier
        """
        pass


class NullMetricsPublisher(IMetricsPublisher):
    """Null object pattern implementation that does nothing."""

    def record_cold_start(self, mcp_server_id: str, duration_s: float, mode: str) -> None:
        """No-op implementation."""
        pass

    def begin_cold_start(self, mcp_server_id: str) -> None:
        """No-op implementation."""
        pass

    def end_cold_start(self, mcp_server_id: str) -> None:
        """No-op implementation."""
        pass
