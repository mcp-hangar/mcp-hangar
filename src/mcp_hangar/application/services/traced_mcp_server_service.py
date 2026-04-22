"""Traced mcp_server service - adds observability to mcp_server operations.

This decorator wraps McpServerService to automatically trace all tool
invocations and health checks with the configured observability backend.

Example:
    service = TracedMcpServerService(
        mcp_server_service=McpServerService(...),
        observability=LangfuseObservabilityAdapter(config),
    )

    # Tool invocations are automatically traced
    result = service.invoke_tool("math", "add", {"a": 1, "b": 2})
"""

import logging
import time
from typing import Any

from ..ports.observability import ObservabilityPort, TraceContext
from ...observability.conventions import MCP, set_governance_attributes
from ...observability.tracing import get_tracer

from .mcp_server_service import McpServerService

logger = logging.getLogger(__name__)


class TracedMcpServerService:
    """Decorator that adds observability tracing to McpServerService.

    Wraps an existing McpServerService instance and automatically traces:
    - Tool invocations with input/output and timing
    - Health checks with results and latency
    - McpServer state transitions

    All tracing is transparent to callers and adds minimal overhead
    when observability is disabled.
    """

    def __init__(
        self,
        observability: ObservabilityPort,
        mcp_server_service: McpServerService | None = None,
        provider_service: McpServerService | None = None,
    ) -> None:
        """Initialize traced service.

        Args:
            mcp_server_service: The underlying mcp_server service to wrap.
            observability: Observability adapter for tracing.
        """
        service = mcp_server_service or provider_service
        if service is None:
            raise TypeError("Missing required argument: mcp_server_service")
        self._service = service
        self._observability = observability

    # --- Delegated methods (no tracing needed) ---

    def list_mcp_servers(self) -> list[dict[str, Any]]:
        """List all mcp_servers with their status."""
        return self._service.list_mcp_servers()

    def start_mcp_server(self, mcp_server_id: str) -> dict[str, Any]:
        """Start a mcp_server."""
        return self._service.start_mcp_server(mcp_server_id)

    def stop_mcp_server(self, mcp_server_id: str) -> dict[str, Any]:
        """Stop a mcp_server."""
        return self._service.stop_mcp_server(mcp_server_id)

    def get_mcp_server_tools(self, mcp_server_id: str) -> dict[str, Any]:
        """Get mcp_server tools."""
        return self._service.get_mcp_server_tools(mcp_server_id)

    # --- Traced methods ---

    def invoke_tool(
        self,
        mcp_server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 30.0,
        trace_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Invoke a tool with full tracing (OTEL span + ObservabilityPort span).

        Args:
            mcp_server_id: McpServer identifier.
            tool_name: Tool name.
            arguments: Tool arguments.
            timeout: Timeout in seconds.
            trace_id: Optional trace ID for correlation.
            user_id: Optional user ID for attribution.
            session_id: Optional session ID for grouping.

        Returns:
            Tool result dictionary.

        Raises:
            McpServerNotFoundError: If mcp_server doesn't exist.
            ToolNotFoundError: If tool doesn't exist.
            ToolInvocationError: If invocation fails.
        """
        tracer = get_tracer(__name__)

        with tracer.start_as_current_span(f"tool.invoke.{tool_name}") as otel_span:
            set_governance_attributes(
                otel_span,
                mcp_server_id=mcp_server_id,
                tool_name=tool_name,
                user_id=user_id,
                session_id=session_id,
            )

            # ObservabilityPort span (Langfuse / partner backend)
            trace_context = None
            if trace_id or user_id or session_id:
                trace_context = TraceContext(
                    trace_id=trace_id or "",
                    user_id=user_id,
                    session_id=session_id,
                )

            obs_span = self._observability.start_tool_span(
                mcp_server_name=mcp_server_id,
                tool_name=tool_name,
                input_params=arguments,
                trace_context=trace_context,
            )

            try:
                result = self._service.invoke_tool(
                    mcp_server_id=mcp_server_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    timeout=timeout,
                )
                otel_span.set_attribute(MCP.TOOL_STATUS, "success")
                obs_span.end_success(output=result)
                return result

            except Exception as e:  # noqa: BLE001 -- fault-barrier: record error in both spans, then re-raise
                otel_span.set_attribute(MCP.TOOL_STATUS, "error")
                otel_span.record_exception(e)
                obs_span.end_error(error=e)
                raise

    def health_check(
        self,
        mcp_server_id: str,
        trace_id: str | None = None,
    ) -> bool:
        """Perform health check with tracing.

        Args:
            mcp_server_id: McpServer identifier.
            trace_id: Optional trace ID to attach result to.

        Returns:
            True if healthy, False otherwise.
        """
        start_time = time.perf_counter()

        try:
            healthy = self._service.health_check(mcp_server_id)
            latency_ms = (time.perf_counter() - start_time) * 1000

            self._observability.record_health_check(
                mcp_server_name=mcp_server_id,
                healthy=healthy,
                latency_ms=latency_ms,
                trace_id=trace_id,
            )

            return healthy

        except Exception as e:  # noqa: BLE001 -- fault-barrier: record error in observability, then re-raise
            latency_ms = (time.perf_counter() - start_time) * 1000

            self._observability.record_health_check(
                mcp_server_name=mcp_server_id,
                healthy=False,
                latency_ms=latency_ms,
                trace_id=trace_id,
            )

            logger.error(
                "Health check failed for mcp_server %s: %s",
                mcp_server_id,
                e,
            )
            raise

    def check_all_health(
        self,
        trace_id: str | None = None,
    ) -> dict[str, bool]:
        """Check health of all mcp_servers with tracing.

        Args:
            trace_id: Optional trace ID to attach results to.

        Returns:
            Dictionary mapping mcp_server_id to health status.
        """
        results = {}

        for mcp_server_status in self._service.list_mcp_servers():
            mcp_server_id = mcp_server_status.get("name") or mcp_server_status.get("mcp_server_id")
            if mcp_server_id:
                try:
                    results[mcp_server_id] = self.health_check(mcp_server_id, trace_id)
                except Exception:  # noqa: BLE001 -- fault-barrier: single mcp_server health check failure must not crash batch check
                    results[mcp_server_id] = False

        return results

    def shutdown_idle_mcp_servers(self) -> list[str]:
        """Shutdown idle mcp_servers."""
        return self._service.shutdown_idle_mcp_servers()

    # --- Observability control ---

    def flush_traces(self) -> None:
        """Flush pending traces to backend."""
        self._observability.flush()

    def shutdown_tracing(self) -> None:
        """Shutdown tracing with final flush."""
        self._observability.shutdown()


# legacy aliases
TracedProviderService = TracedMcpServerService
