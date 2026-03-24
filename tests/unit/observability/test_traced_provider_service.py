"""Tests for TracedProviderService.

Verifies that the traced service correctly wraps provider operations
and records observability data.
"""

from unittest.mock import MagicMock

import pytest

from mcp_hangar.application.ports.observability import NullObservabilityAdapter
from mcp_hangar.application.services.traced_provider_service import TracedProviderService


class MockProviderService:
    """Mock ProviderService for testing."""

    def __init__(self) -> None:
        self.invoke_tool_result: dict = {"result": "success"}
        self.invoke_tool_error: Exception | None = None
        self.health_check_result: bool = True
        self.health_check_error: Exception | None = None
        self.list_providers_result: list = []

    def list_providers(self) -> list:
        return self.list_providers_result

    def start_provider(self, provider_id: str) -> dict:
        return {"provider": provider_id, "state": "READY"}

    def stop_provider(self, provider_id: str) -> dict:
        return {"stopped": provider_id}

    def get_provider_tools(self, provider_id: str) -> dict:
        return {"provider": provider_id, "tools": []}

    def invoke_tool(
        self,
        provider_id: str,
        tool_name: str,
        arguments: dict,
        timeout: float = 30.0,
    ) -> dict:
        if self.invoke_tool_error:
            raise self.invoke_tool_error
        return self.invoke_tool_result

    def health_check(self, provider_id: str) -> bool:
        if self.health_check_error:
            raise self.health_check_error
        return self.health_check_result

    def shutdown_idle_providers(self) -> list:
        return []


class TestTracedProviderServiceDelegation:
    """Tests for method delegation."""

    def test_list_providers_delegates(self) -> None:
        """list_providers delegates to underlying service."""
        mock_service = MockProviderService()
        mock_service.list_providers_result = [{"name": "math"}]

        traced = TracedProviderService(
            provider_service=mock_service,
            observability=NullObservabilityAdapter(),
        )

        result = traced.list_providers()

        assert result == [{"name": "math"}]

    def test_start_provider_delegates(self) -> None:
        """start_provider delegates to underlying service."""
        mock_service = MockProviderService()
        traced = TracedProviderService(
            provider_service=mock_service,
            observability=NullObservabilityAdapter(),
        )

        result = traced.start_provider("math")

        assert result == {"provider": "math", "state": "READY"}

    def test_stop_provider_delegates(self) -> None:
        """stop_provider delegates to underlying service."""
        mock_service = MockProviderService()
        traced = TracedProviderService(
            provider_service=mock_service,
            observability=NullObservabilityAdapter(),
        )

        result = traced.stop_provider("math")

        assert result == {"stopped": "math"}

    def test_get_provider_tools_delegates(self) -> None:
        """get_provider_tools delegates to underlying service."""
        mock_service = MockProviderService()
        traced = TracedProviderService(
            provider_service=mock_service,
            observability=NullObservabilityAdapter(),
        )

        result = traced.get_provider_tools("math")

        assert result == {"provider": "math", "tools": []}


class TestTracedProviderServiceTracing:
    """Tests for tracing behavior."""

    def test_invoke_tool_creates_span(self) -> None:
        """invoke_tool creates a traced span."""
        mock_service = MockProviderService()
        mock_observability = MagicMock()
        mock_span = MagicMock()
        mock_observability.start_tool_span.return_value = mock_span

        traced = TracedProviderService(
            provider_service=mock_service,
            observability=mock_observability,
        )

        result = traced.invoke_tool("math", "add", {"a": 1, "b": 2})

        # Verify span was started
        mock_observability.start_tool_span.assert_called_once_with(
            provider_name="math",
            tool_name="add",
            input_params={"a": 1, "b": 2},
            trace_context=None,
        )

        # Verify span was ended with success
        mock_span.end_success.assert_called_once_with(output={"result": "success"})

        assert result == {"result": "success"}

    def test_invoke_tool_ends_span_on_error(self) -> None:
        """invoke_tool ends span with error on failure."""
        mock_service = MockProviderService()
        mock_service.invoke_tool_error = ValueError("Test error")

        mock_observability = MagicMock()
        mock_span = MagicMock()
        mock_observability.start_tool_span.return_value = mock_span

        traced = TracedProviderService(
            provider_service=mock_service,
            observability=mock_observability,
        )

        with pytest.raises(ValueError):
            traced.invoke_tool("math", "add", {"a": 1})

        # Verify span was ended with error
        mock_span.end_error.assert_called_once()
        error_arg = mock_span.end_error.call_args[1]["error"]
        assert isinstance(error_arg, ValueError)

    def test_invoke_tool_propagates_trace_context(self) -> None:
        """invoke_tool passes trace context to observability."""
        mock_service = MockProviderService()
        mock_observability = MagicMock()
        mock_span = MagicMock()
        mock_observability.start_tool_span.return_value = mock_span

        traced = TracedProviderService(
            provider_service=mock_service,
            observability=mock_observability,
        )

        traced.invoke_tool(
            "math",
            "add",
            {"a": 1},
            trace_id="trace-123",
            user_id="user-456",
            session_id="session-789",
        )

        # Verify trace context was passed
        call_args = mock_observability.start_tool_span.call_args
        trace_context = call_args.kwargs["trace_context"]

        assert trace_context.trace_id == "trace-123"
        assert trace_context.user_id == "user-456"
        assert trace_context.session_id == "session-789"

    def test_health_check_records_result(self) -> None:
        """health_check records result in observability."""
        mock_service = MockProviderService()
        mock_service.health_check_result = True

        mock_observability = MagicMock()

        traced = TracedProviderService(
            provider_service=mock_service,
            observability=mock_observability,
        )

        result = traced.health_check("math")

        assert result is True

        # Verify health check was recorded
        mock_observability.record_health_check.assert_called_once()
        call_args = mock_observability.record_health_check.call_args

        assert call_args.kwargs["provider_name"] == "math"
        assert call_args.kwargs["healthy"] is True
        assert call_args.kwargs["latency_ms"] >= 0

    def test_health_check_records_failure(self) -> None:
        """health_check records failure in observability."""
        mock_service = MockProviderService()
        mock_service.health_check_error = ConnectionError("Timeout")

        mock_observability = MagicMock()

        traced = TracedProviderService(
            provider_service=mock_service,
            observability=mock_observability,
        )

        with pytest.raises(ConnectionError):
            traced.health_check("math")

        # Verify failure was recorded
        mock_observability.record_health_check.assert_called_once()
        call_args = mock_observability.record_health_check.call_args

        assert call_args.kwargs["provider_name"] == "math"
        assert call_args.kwargs["healthy"] is False


class TestTracedProviderServiceObservabilityControl:
    """Tests for observability control methods."""

    def test_flush_traces_calls_flush(self) -> None:
        """flush_traces calls observability flush."""
        mock_service = MockProviderService()
        mock_observability = MagicMock()

        traced = TracedProviderService(
            provider_service=mock_service,
            observability=mock_observability,
        )

        traced.flush_traces()

        mock_observability.flush.assert_called_once()

    def test_shutdown_tracing_calls_shutdown(self) -> None:
        """shutdown_tracing calls observability shutdown."""
        mock_service = MockProviderService()
        mock_observability = MagicMock()

        traced = TracedProviderService(
            provider_service=mock_service,
            observability=mock_observability,
        )

        traced.shutdown_tracing()

        mock_observability.shutdown.assert_called_once()
