"""Tests that TracedProviderService creates OTEL spans with governance attributes."""

import pytest
from unittest.mock import MagicMock, patch

from mcp_hangar.application.services.traced_provider_service import TracedProviderService
from mcp_hangar.application.ports.observability import NullObservabilityAdapter
from mcp_hangar.observability.conventions import Enforcement, MCP, Provider


def _make_service(invoke_result: dict | None = None, invoke_raises: Exception | None = None):
    """Build a TracedProviderService with a mocked underlying service."""
    mock_service = MagicMock()
    if invoke_raises:
        mock_service.invoke_tool.side_effect = invoke_raises
    else:
        mock_service.invoke_tool.return_value = invoke_result or {"content": "ok"}

    return TracedProviderService(
        provider_service=mock_service,
        observability=NullObservabilityAdapter(),
    )


class TestTracedProviderServiceOtelSpan:
    """TracedProviderService must create OTEL spans for tool invocations."""

    def test_invoke_tool_creates_otel_span(self) -> None:
        """invoke_tool creates an OTEL span named 'tool.invoke'."""
        svc = _make_service()

        with patch("mcp_hangar.application.services.traced_provider_service.get_tracer") as mock_tracer_fn:
            mock_tracer = MagicMock()
            mock_span = MagicMock()
            mock_span.__enter__ = MagicMock(return_value=mock_span)
            mock_span.__exit__ = MagicMock(return_value=False)
            mock_tracer.start_as_current_span.return_value = mock_span
            mock_tracer_fn.return_value = mock_tracer

            svc.invoke_tool("math", "add", {"a": 1, "b": 2})

            mock_tracer.start_as_current_span.assert_called_once()
            span_name = mock_tracer.start_as_current_span.call_args[0][0]
            assert "tool" in span_name or "invoke" in span_name

    def test_invoke_tool_span_carries_governance_attributes(self) -> None:
        """invoke_tool span carries Provider.ID and MCP.TOOL_NAME attributes."""
        svc = _make_service()

        with patch("mcp_hangar.application.services.traced_provider_service.get_tracer") as mock_tracer_fn:
            mock_tracer = MagicMock()
            mock_span = MagicMock()
            mock_span.__enter__ = MagicMock(return_value=mock_span)
            mock_span.__exit__ = MagicMock(return_value=False)
            mock_tracer.start_as_current_span.return_value = mock_span
            mock_tracer_fn.return_value = mock_tracer

            svc.invoke_tool("math", "add", {"a": 1})

            set_calls = {c.args[0]: c.args[1] for c in mock_span.set_attribute.call_args_list}
            assert set_calls.get(Provider.ID) == "math"
            assert set_calls.get(MCP.TOOL_NAME) == "add"

    def test_invoke_tool_span_sets_success_status(self) -> None:
        """Successful invocation sets MCP.TOOL_STATUS to 'success'."""
        svc = _make_service()

        with patch("mcp_hangar.application.services.traced_provider_service.get_tracer") as mock_tracer_fn:
            mock_tracer = MagicMock()
            mock_span = MagicMock()
            mock_span.__enter__ = MagicMock(return_value=mock_span)
            mock_span.__exit__ = MagicMock(return_value=False)
            mock_tracer.start_as_current_span.return_value = mock_span
            mock_tracer_fn.return_value = mock_tracer

            svc.invoke_tool("math", "add", {})

            set_calls = {c.args[0]: c.args[1] for c in mock_span.set_attribute.call_args_list}
            assert set_calls.get(MCP.TOOL_STATUS) == "success"

    def test_invoke_tool_span_sets_error_on_failure(self) -> None:
        """Failed invocation sets MCP.TOOL_STATUS to 'error' and records exception."""
        from mcp_hangar.domain.exceptions import ToolInvocationError

        svc = _make_service(invoke_raises=ToolInvocationError("math", "add", "boom"))

        with patch("mcp_hangar.application.services.traced_provider_service.get_tracer") as mock_tracer_fn:
            mock_tracer = MagicMock()
            mock_span = MagicMock()
            mock_span.__enter__ = MagicMock(return_value=mock_span)
            mock_span.__exit__ = MagicMock(return_value=False)
            mock_tracer.start_as_current_span.return_value = mock_span
            mock_tracer_fn.return_value = mock_tracer

            with pytest.raises(ToolInvocationError):
                svc.invoke_tool("math", "add", {})

            set_calls = {c.args[0]: c.args[1] for c in mock_span.set_attribute.call_args_list}
            assert set_calls.get(MCP.TOOL_STATUS) == "error"
            mock_span.record_exception.assert_called_once()

    def test_invoke_tool_span_carries_user_and_session_when_provided(self) -> None:
        """user_id and session_id appear as span attributes when provided."""
        svc = _make_service()

        with patch("mcp_hangar.application.services.traced_provider_service.get_tracer") as mock_tracer_fn:
            mock_tracer = MagicMock()
            mock_span = MagicMock()
            mock_span.__enter__ = MagicMock(return_value=mock_span)
            mock_span.__exit__ = MagicMock(return_value=False)
            mock_tracer.start_as_current_span.return_value = mock_span
            mock_tracer_fn.return_value = mock_tracer

            svc.invoke_tool("math", "add", {}, user_id="alice", session_id="sess-42")

            set_calls = {c.args[0]: c.args[1] for c in mock_span.set_attribute.call_args_list}
            assert set_calls.get(MCP.USER_ID) == "alice"
            assert set_calls.get(MCP.SESSION_ID) == "sess-42"
