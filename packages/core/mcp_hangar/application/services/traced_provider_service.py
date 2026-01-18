"""Traced provider service - adds observability to provider operations.

This decorator wraps ProviderService to automatically trace all tool
invocations and health checks with the configured observability backend.

Example:
    service = TracedProviderService(
        provider_service=ProviderService(...),
        observability=LangfuseObservabilityAdapter(config),
    )

    # Tool invocations are automatically traced
    result = service.invoke_tool("math", "add", {"a": 1, "b": 2})
"""

import logging
import time
from typing import Any

from ..ports.observability import ObservabilityPort, TraceContext

logger = logging.getLogger(__name__)


class TracedProviderService:
    """Decorator that adds observability tracing to ProviderService.

    Wraps an existing ProviderService instance and automatically traces:
    - Tool invocations with input/output and timing
    - Health checks with results and latency
    - Provider state transitions

    All tracing is transparent to callers and adds minimal overhead
    when observability is disabled.
    """

    def __init__(
        self,
        provider_service: "ProviderService",  # noqa: F821
        observability: ObservabilityPort,
    ) -> None:
        """Initialize traced service.

        Args:
            provider_service: The underlying provider service to wrap.
            observability: Observability adapter for tracing.
        """
        self._service = provider_service
        self._observability = observability

    # --- Delegated methods (no tracing needed) ---

    def list_providers(self) -> list[dict[str, Any]]:
        """List all providers with their status."""
        return self._service.list_providers()

    def start_provider(self, provider_id: str) -> dict[str, Any]:
        """Start a provider."""
        return self._service.start_provider(provider_id)

    def stop_provider(self, provider_id: str) -> dict[str, Any]:
        """Stop a provider."""
        return self._service.stop_provider(provider_id)

    def get_provider_tools(self, provider_id: str) -> dict[str, Any]:
        """Get provider tools."""
        return self._service.get_provider_tools(provider_id)

    # --- Traced methods ---

    def invoke_tool(
        self,
        provider_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 30.0,
        trace_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Invoke a tool with full tracing.

        Args:
            provider_id: Provider identifier.
            tool_name: Tool name.
            arguments: Tool arguments.
            timeout: Timeout in seconds.
            trace_id: Optional trace ID for correlation.
            user_id: Optional user ID for attribution.
            session_id: Optional session ID for grouping.

        Returns:
            Tool result dictionary.

        Raises:
            ProviderNotFoundError: If provider doesn't exist.
            ToolNotFoundError: If tool doesn't exist.
            ToolInvocationError: If invocation fails.
        """
        trace_context = None
        if trace_id or user_id or session_id:
            trace_context = TraceContext(
                trace_id=trace_id or "",
                user_id=user_id,
                session_id=session_id,
            )

        span = self._observability.start_tool_span(
            provider_name=provider_id,
            tool_name=tool_name,
            input_params=arguments,
            trace_context=trace_context,
        )

        try:
            result = self._service.invoke_tool(
                provider_id=provider_id,
                tool_name=tool_name,
                arguments=arguments,
                timeout=timeout,
            )
            span.end_success(output=result)
            return result

        except Exception as e:
            span.end_error(error=e)
            raise

    def health_check(
        self,
        provider_id: str,
        trace_id: str | None = None,
    ) -> bool:
        """Perform health check with tracing.

        Args:
            provider_id: Provider identifier.
            trace_id: Optional trace ID to attach result to.

        Returns:
            True if healthy, False otherwise.
        """
        start_time = time.perf_counter()

        try:
            healthy = self._service.health_check(provider_id)
            latency_ms = (time.perf_counter() - start_time) * 1000

            self._observability.record_health_check(
                provider_name=provider_id,
                healthy=healthy,
                latency_ms=latency_ms,
                trace_id=trace_id,
            )

            return healthy

        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000

            self._observability.record_health_check(
                provider_name=provider_id,
                healthy=False,
                latency_ms=latency_ms,
                trace_id=trace_id,
            )

            logger.error(
                "Health check failed for provider %s: %s",
                provider_id,
                e,
            )
            raise

    def check_all_health(
        self,
        trace_id: str | None = None,
    ) -> dict[str, bool]:
        """Check health of all providers with tracing.

        Args:
            trace_id: Optional trace ID to attach results to.

        Returns:
            Dictionary mapping provider_id to health status.
        """
        results = {}

        for provider_status in self._service.list_providers():
            provider_id = provider_status.get("name") or provider_status.get("provider_id")
            if provider_id:
                try:
                    results[provider_id] = self.health_check(provider_id, trace_id)
                except Exception:
                    results[provider_id] = False

        return results

    def shutdown_idle_providers(self) -> list[str]:
        """Shutdown idle providers."""
        return self._service.shutdown_idle_providers()

    # --- Observability control ---

    def flush_traces(self) -> None:
        """Flush pending traces to backend."""
        self._observability.flush()

    def shutdown_tracing(self) -> None:
        """Shutdown tracing with final flush."""
        self._observability.shutdown()
