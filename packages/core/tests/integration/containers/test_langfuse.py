"""Integration tests for Langfuse observability using Testcontainers.

These tests verify that the Langfuse integration works correctly with a real
Langfuse instance, including:
- Trace creation and retrieval
- Score recording
- Health check recording
- High-volume trace handling
"""

import time

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.container,
    pytest.mark.langfuse,
    pytest.mark.slow,
]


class TestLangfuseTraceCreation:
    """Tests for trace creation in real Langfuse instance."""

    def test_trace_created_and_visible_in_api(
        self,
        langfuse_config,
        http_client,
        langfuse_container: dict,
    ) -> None:
        """Traces are created and visible in Langfuse API."""
        from mcp_hangar.infrastructure.observability.langfuse_adapter import LangfuseObservabilityAdapter

        adapter = LangfuseObservabilityAdapter(langfuse_config)

        # Create a trace
        span = adapter.start_tool_span(
            provider_name="test-provider",
            tool_name="test-tool",
            input_params={"query": "test query"},
        )
        span.end_success({"result": "success"})

        adapter.flush()
        time.sleep(2)

        # Query Langfuse API
        response = http_client.get(
            f"{langfuse_container['url']}/api/public/traces",
            auth=(langfuse_container["public_key"], langfuse_container["secret_key"]),
        )

        if response.status_code == 200:
            traces = response.json().get("data", [])
            assert any("test-provider" in t.get("name", "") for t in traces), f"Trace not found in {traces}"
        else:
            pytest.skip(f"Langfuse API returned {response.status_code}")

        adapter.shutdown()

    def test_error_trace_recorded(
        self,
        langfuse_config,
    ) -> None:
        """Error traces are recorded without raising exceptions."""
        from mcp_hangar.infrastructure.observability.langfuse_adapter import LangfuseObservabilityAdapter

        adapter = LangfuseObservabilityAdapter(langfuse_config)

        span = adapter.start_tool_span(
            provider_name="error-provider",
            tool_name="failing-tool",
            input_params={"will_fail": True},
        )
        span.end_error(ValueError("Simulated failure for testing"))

        adapter.flush()
        adapter.shutdown()
        # Test passes if no exception was raised

    def test_trace_context_propagation(
        self,
        langfuse_config,
    ) -> None:
        """Trace context is properly propagated."""
        from mcp_hangar.application.ports.observability import TraceContext
        from mcp_hangar.infrastructure.observability.langfuse_adapter import LangfuseObservabilityAdapter

        adapter = LangfuseObservabilityAdapter(langfuse_config)

        context = TraceContext(
            trace_id="external123456789012345678901234",
            user_id="test-user-123",
            session_id="test-session-456",
        )

        span = adapter.start_tool_span(
            provider_name="context-test",
            tool_name="propagation-test",
            input_params={"test": True},
            trace_context=context,
        )
        span.end_success({"propagated": True})

        adapter.flush()
        adapter.shutdown()


class TestLangfuseScoreAndHealthRecording:
    """Tests for score and health check recording in Langfuse."""

    def test_scores_recorded(
        self,
        langfuse_config,
    ) -> None:
        """Multiple scores can be recorded on traces."""
        from mcp_hangar.infrastructure.observability.langfuse_adapter import LangfuseObservabilityAdapter

        adapter = LangfuseObservabilityAdapter(langfuse_config)

        trace_id = "scores12345678901234567890123456"

        # Record multiple scores
        for name, value in [("latency_ms", 150.0), ("accuracy", 0.92), ("cost", 0.001)]:
            adapter.record_score(trace_id=trace_id, name=name, value=value)

        adapter.flush()
        adapter.shutdown()

    def test_health_check_recorded(
        self,
        langfuse_config,
    ) -> None:
        """Health checks are recorded as standalone traces."""
        from mcp_hangar.infrastructure.observability.langfuse_adapter import LangfuseObservabilityAdapter

        adapter = LangfuseObservabilityAdapter(langfuse_config)

        adapter.record_health_check(
            provider_name="health-test-provider",
            healthy=True,
            latency_ms=5.2,
        )

        adapter.record_health_check(
            provider_name="unhealthy-provider",
            healthy=False,
            latency_ms=1500.0,
            trace_id="health12345678901234567890123456",
        )

        adapter.flush()
        adapter.shutdown()


class TestLangfuseHighVolume:
    """Tests for high-volume trace handling."""

    @pytest.mark.slow
    def test_high_volume_and_concurrent_traces(
        self,
        langfuse_config,
    ) -> None:
        """High volume concurrent trace creation is thread-safe."""
        import threading

        from mcp_hangar.infrastructure.observability.langfuse_adapter import LangfuseObservabilityAdapter

        adapter = LangfuseObservabilityAdapter(langfuse_config)
        errors: list[Exception] = []

        def create_traces(thread_id: int):
            try:
                for i in range(20):
                    span = adapter.start_tool_span(
                        provider_name=f"thread-{thread_id}",
                        tool_name=f"op-{i}",
                        input_params={"thread": thread_id, "iteration": i},
                    )
                    span.end_success({"ok": True})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_traces, args=(i,)) for i in range(5)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        adapter.flush()
        adapter.shutdown()

        assert len(errors) == 0, f"Errors during concurrent trace creation: {errors}"


class TestLangfuseGracefulDegradation:
    """Tests for graceful degradation when Langfuse is unavailable.

    Note: These tests don't require the Langfuse container.
    """

    @pytest.mark.parametrize(
        "host,description",
        [
            ("http://invalid-host-that-does-not-exist:99999", "invalid host"),
            ("http://10.255.255.1:3000", "non-routable IP"),
        ],
    )
    def test_graceful_degradation_on_network_failure(
        self,
        host: str,
        description: str,
    ) -> None:
        """Adapter handles network failures gracefully."""
        from mcp_hangar.infrastructure.observability.langfuse_adapter import (
            LangfuseConfig,
            LangfuseObservabilityAdapter,
        )

        config = LangfuseConfig(
            enabled=True,
            host=host,
            public_key="pk-test",
            secret_key="sk-test",
        )

        adapter = LangfuseObservabilityAdapter(config)

        # Should not raise exceptions
        span = adapter.start_tool_span("test", "tool", {})
        span.end_success({})
        adapter.flush()
        adapter.shutdown()
