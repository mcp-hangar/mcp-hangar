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

    @abstractmethod
    def set_connection_active(self, mcp_server_id: str, active: bool) -> None:
        """
        Record whether a backend connection is currently established.

        Args:
            mcp_server_id: McpServer identifier
            active: True on connect, False on teardown
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

    def set_connection_active(self, mcp_server_id: str, active: bool) -> None:
        """No-op implementation."""
        pass


# The publisher the domain uses when a caller does not inject one.
#
# It starts as the Null object so importing the domain never reaches for an
# adapter, and the composition root swaps in the real one at bootstrap. That
# swap is the part that was missing: `PrometheusMetricsPublisher` existed and
# was never constructed anywhere, so every McpServer silently used the Null
# object and `mcp_hangar_mcp_server_cold_start_seconds` -- documented in
# metrics.py as the critical UX metric -- was never observed.
_default_metrics_publisher: IMetricsPublisher = NullMetricsPublisher()


def set_default_metrics_publisher(publisher: IMetricsPublisher) -> None:
    """Install the publisher used when none is injected. Called from bootstrap."""
    global _default_metrics_publisher
    _default_metrics_publisher = publisher


def get_default_metrics_publisher() -> IMetricsPublisher:
    """The publisher used when none is injected."""
    return _default_metrics_publisher
