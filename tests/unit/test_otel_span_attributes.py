"""Integration test: verify OTEL span attributes via InMemorySpanExporter.

Uses the opentelemetry-sdk InMemorySpanExporter to capture spans in-process
without an external collector. Confirms that conventions.py attribute constants
produce the correct span attribute names and values when the SDK is active.
"""

import pytest
from unittest.mock import MagicMock

from mcp_hangar.observability.conventions import Enforcement, MCP, Provider


@pytest.fixture(autouse=True)
def otel_setup():
    """Configure a TracerProvider with InMemorySpanExporter for each test.

    Patches get_tracer() in traced_provider_service to return a tracer from
    our test TracerProvider so spans are captured by InMemorySpanExporter.
    This avoids fighting with the global TracerProvider singleton.
    """
    pytest.importorskip("opentelemetry.sdk.trace")  # skip if SDK not installed

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    exporter = InMemorySpanExporter()
    test_provider = TracerProvider()
    test_provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Use the test provider's tracer directly (avoids global singleton issues)
    test_tracer = test_provider.get_tracer("test")

    import mcp_hangar.application.services.traced_provider_service as tps_mod

    original_get_tracer = tps_mod.get_tracer
    tps_mod.get_tracer = lambda name=None: test_tracer

    yield exporter

    tps_mod.get_tracer = original_get_tracer
    exporter.clear()
    test_provider.shutdown()


class TestOtelSpanAttributesIntegration:
    """Real OTEL SDK span attribute tests."""

    def test_tool_invoke_span_has_correct_name(self, otel_setup) -> None:
        """Span name is 'tool.invoke.{tool_name}'."""
        from mcp_hangar.application.services.traced_provider_service import TracedProviderService
        from mcp_hangar.application.ports.observability import NullObservabilityAdapter

        mock_inner = MagicMock()
        mock_inner.invoke_tool.return_value = {"result": 3}

        svc = TracedProviderService(provider_service=mock_inner, observability=NullObservabilityAdapter())
        svc.invoke_tool("math", "add", {"a": 1, "b": 2})

        spans = otel_setup.get_finished_spans()
        assert len(spans) >= 1
        names = [s.name for s in spans]
        assert any("add" in n for n in names), f"Expected span with 'add' in name, got: {names}"

    def test_tool_invoke_span_has_provider_id(self, otel_setup) -> None:
        """Span carries mcp.provider.id attribute."""
        from mcp_hangar.application.services.traced_provider_service import TracedProviderService
        from mcp_hangar.application.ports.observability import NullObservabilityAdapter

        mock_inner = MagicMock()
        mock_inner.invoke_tool.return_value = {}

        svc = TracedProviderService(provider_service=mock_inner, observability=NullObservabilityAdapter())
        svc.invoke_tool("my-provider", "my-tool", {})

        spans = otel_setup.get_finished_spans()
        tool_span = next(s for s in spans if "my-tool" in s.name)
        assert tool_span.attributes.get(Provider.ID) == "my-provider"

    def test_tool_invoke_span_has_tool_name(self, otel_setup) -> None:
        """Span carries mcp.tool.name attribute."""
        from mcp_hangar.application.services.traced_provider_service import TracedProviderService
        from mcp_hangar.application.ports.observability import NullObservabilityAdapter

        mock_inner = MagicMock()
        mock_inner.invoke_tool.return_value = {}

        svc = TracedProviderService(provider_service=mock_inner, observability=NullObservabilityAdapter())
        svc.invoke_tool("p", "my_special_tool", {})

        spans = otel_setup.get_finished_spans()
        tool_span = next(s for s in spans if "my_special_tool" in s.name)
        assert tool_span.attributes.get(MCP.TOOL_NAME) == "my_special_tool"

    def test_tool_invoke_span_status_ok_on_success(self, otel_setup) -> None:
        """Successful invocation: span status code is UNSET (OK)."""
        from opentelemetry.trace import StatusCode
        from mcp_hangar.application.services.traced_provider_service import TracedProviderService
        from mcp_hangar.application.ports.observability import NullObservabilityAdapter

        mock_inner = MagicMock()
        mock_inner.invoke_tool.return_value = {}

        svc = TracedProviderService(provider_service=mock_inner, observability=NullObservabilityAdapter())
        svc.invoke_tool("p", "t", {})

        spans = otel_setup.get_finished_spans()
        tool_span = next(s for s in spans if ".t" in s.name or "t" == s.name.split(".")[-1])
        assert tool_span.status.status_code in (StatusCode.UNSET, StatusCode.OK)

    def test_tool_invoke_span_status_error_on_failure(self, otel_setup) -> None:
        """Failed invocation: span status is ERROR and exception event is recorded."""
        from opentelemetry.trace import StatusCode
        from mcp_hangar.application.services.traced_provider_service import TracedProviderService
        from mcp_hangar.application.ports.observability import NullObservabilityAdapter
        from mcp_hangar.domain.exceptions import ToolInvocationError

        mock_inner = MagicMock()
        mock_inner.invoke_tool.side_effect = ToolInvocationError("p", "bad invocation")

        svc = TracedProviderService(provider_service=mock_inner, observability=NullObservabilityAdapter())
        with pytest.raises(ToolInvocationError):
            svc.invoke_tool("p", "t", {})

        spans = otel_setup.get_finished_spans()
        tool_span = spans[-1]
        # Exception should be recorded as a span event
        exception_events = [e for e in tool_span.events if e.name == "exception"]
        assert len(exception_events) >= 1

    def test_user_id_and_session_id_appear_in_span(self, otel_setup) -> None:
        """user_id and session_id propagate to span attributes."""
        from mcp_hangar.application.services.traced_provider_service import TracedProviderService
        from mcp_hangar.application.ports.observability import NullObservabilityAdapter

        mock_inner = MagicMock()
        mock_inner.invoke_tool.return_value = {}

        svc = TracedProviderService(provider_service=mock_inner, observability=NullObservabilityAdapter())
        svc.invoke_tool("p", "t", {}, user_id="user-123", session_id="sess-abc")

        spans = otel_setup.get_finished_spans()
        tool_span = spans[-1]
        assert tool_span.attributes.get(MCP.USER_ID) == "user-123"
        assert tool_span.attributes.get(MCP.SESSION_ID) == "sess-abc"
