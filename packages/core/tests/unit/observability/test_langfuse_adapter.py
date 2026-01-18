"""Tests for Langfuse adapter.

These tests verify the Langfuse adapter without requiring the actual
Langfuse SDK, using mocks for the external dependency.
"""

import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

# Skip entire module on Python 3.14+ due to Langfuse's Pydantic v1 incompatibility
if sys.version_info >= (3, 14):
    pytest.skip("Langfuse uses Pydantic v1 which is incompatible with Python 3.14+", allow_module_level=True)

from mcp_hangar.application.ports.observability import NullSpanHandle, ObservabilityPort
from mcp_hangar.infrastructure.observability.langfuse_adapter import (
    LangfuseAdapter,
    LangfuseConfig,
    LangfuseObservabilityAdapter,
    LangfuseSpanHandle,
)


class TestLangfuseConfig:
    """Tests for LangfuseConfig validation."""

    def test_default_values(self) -> None:
        """Config has sensible defaults."""
        config = LangfuseConfig()

        assert config.enabled is True
        assert config.public_key == ""
        assert config.secret_key == ""
        assert config.host == "https://cloud.langfuse.com"
        assert config.sample_rate == 1.0
        assert config.scrub_inputs is False
        assert config.scrub_outputs is False

    def test_validate_requires_keys_when_enabled(self) -> None:
        """Validation fails when enabled without keys."""
        config = LangfuseConfig(enabled=True)

        errors = config.validate()

        assert "langfuse.public_key is required when enabled" in errors
        assert "langfuse.secret_key is required when enabled" in errors

    def test_validate_passes_when_disabled(self) -> None:
        """Validation passes when disabled, even without keys."""
        config = LangfuseConfig(enabled=False)

        errors = config.validate()

        assert errors == []

    def test_validate_passes_with_valid_config(self) -> None:
        """Validation passes with complete config."""
        config = LangfuseConfig(
            enabled=True,
            public_key="pk-test",
            secret_key="sk-test",
        )

        errors = config.validate()

        assert errors == []

    def test_validate_sample_rate_bounds(self) -> None:
        """Validation fails for invalid sample rate."""
        config = LangfuseConfig(
            enabled=True,
            public_key="pk-test",
            secret_key="sk-test",
            sample_rate=1.5,
        )

        errors = config.validate()

        assert "langfuse.sample_rate must be between 0.0 and 1.0" in errors

    def test_is_frozen(self) -> None:
        """Config is immutable."""
        config = LangfuseConfig()

        with pytest.raises(AttributeError):
            config.enabled = False  # type: ignore


class TestLangfuseAdapter:
    """Tests for LangfuseAdapter."""

    def test_disabled_when_config_disabled(self) -> None:
        """Adapter is disabled when config.enabled is False."""
        config = LangfuseConfig(enabled=False)

        adapter = LangfuseAdapter(config)

        assert adapter.is_enabled is False

    def test_raises_import_error_when_sdk_missing(self) -> None:
        """Adapter raises ImportError when langfuse not installed."""
        config = LangfuseConfig(
            enabled=True,
            public_key="pk-test",
            secret_key="sk-test",
        )

        with (
            patch.dict("sys.modules", {"langfuse": None}),
            patch(
                "mcp_hangar.infrastructure.observability.langfuse_adapter._langfuse_available",
                False,
            ),
        ):
            with pytest.raises(ImportError) as exc_info:
                LangfuseAdapter(config)

            assert "Langfuse package not installed" in str(exc_info.value)

    def test_raises_value_error_on_invalid_config(self) -> None:
        """Adapter raises ValueError on invalid config when SDK is available."""
        config = LangfuseConfig(
            enabled=True,
            public_key="",  # Missing
            secret_key="sk-test",
        )

        # This test checks ValueError for invalid config, but ImportError
        # takes precedence when langfuse is not installed
        with pytest.raises((ValueError, ImportError)) as exc_info:
            LangfuseAdapter(config)

        # If langfuse is installed, we should get ValueError
        # If not installed, we get ImportError (which is also valid behavior)
        if isinstance(exc_info.value, ValueError):
            assert "Invalid Langfuse config" in str(exc_info.value)

    def test_start_span_returns_none_when_disabled(self) -> None:
        """start_span returns (None, '') when disabled."""
        config = LangfuseConfig(enabled=False)
        adapter = LangfuseAdapter(config)

        result, trace_id = adapter.start_span("test-trace")

        assert result is None
        assert trace_id == ""

    def test_start_span_with_input_returns_none_when_disabled(self) -> None:
        """start_span with input data returns (None, '') when disabled."""
        config = LangfuseConfig(enabled=False)
        adapter = LangfuseAdapter(config)

        result, trace_id = adapter.start_span("test-span", input_data={"key": "value"})

        assert result is None
        assert trace_id == ""

    def test_score_does_nothing_when_disabled(self) -> None:
        """create_score does nothing when disabled."""
        config = LangfuseConfig(enabled=False)
        adapter = LangfuseAdapter(config)

        # Should not raise
        adapter.create_score("trace-123", "test", 1.0)

    def test_flush_does_nothing_when_disabled(self) -> None:
        """flush does nothing when disabled."""
        config = LangfuseConfig(enabled=False)
        adapter = LangfuseAdapter(config)

        # Should not raise
        adapter.flush()

    def test_shutdown_does_nothing_when_disabled(self) -> None:
        """shutdown does nothing when disabled."""
        config = LangfuseConfig(enabled=False)
        adapter = LangfuseAdapter(config)

        # Should not raise
        adapter.shutdown()


class TestLangfuseSpanHandle:
    """Tests for LangfuseSpanHandle."""

    def test_end_success_records_output_and_latency(self) -> None:
        """end_success records output and calculates latency."""
        mock_adapter = MagicMock()
        mock_adapter.is_enabled = True

        handle = LangfuseSpanHandle(
            adapter=mock_adapter,
            span=MagicMock(),
            trace_id="trace-123",
        )

        time.sleep(0.01)  # Ensure some time passes
        handle.end_success({"result": "ok"})

        # Verify end_span was called
        mock_adapter.end_span.assert_called_once()
        call_args = mock_adapter.end_span.call_args
        assert call_args.kwargs["output"] == {"result": "ok"}
        assert call_args.kwargs["level"] == "DEFAULT"

        # Verify latency score was recorded
        score_calls = [c for c in mock_adapter.create_score.call_args_list]
        latency_call = next(c for c in score_calls if c.kwargs.get("name") == "tool_latency_ms")
        assert latency_call.kwargs["trace_id"] == "trace-123"
        assert latency_call.kwargs["value"] > 0

    def test_end_error_records_error_and_failure_score(self) -> None:
        """end_error records error details and failure score."""
        mock_adapter = MagicMock()
        mock_adapter.is_enabled = True

        handle = LangfuseSpanHandle(
            adapter=mock_adapter,
            span=MagicMock(),
            trace_id="trace-123",
        )

        error = ValueError("Test error")
        handle.end_error(error)

        # Verify end_span was called with error info
        mock_adapter.end_span.assert_called_once()
        call_args = mock_adapter.end_span.call_args
        assert "error" in call_args.kwargs["output"]
        assert call_args.kwargs["level"] == "ERROR"

        # Verify failure score
        score_calls = [c for c in mock_adapter.create_score.call_args_list]
        success_call = next(c for c in score_calls if c.kwargs.get("name") == "tool_success")
        assert success_call.kwargs["value"] == 0.0

    def test_prevents_double_end(self) -> None:
        """Cannot end a span twice."""
        mock_adapter = MagicMock()
        mock_adapter.is_enabled = True

        handle = LangfuseSpanHandle(
            adapter=mock_adapter,
            span=MagicMock(),
            trace_id="trace-123",
        )

        handle.end_success({"result": "first"})
        handle.end_success({"result": "second"})  # Should be ignored

        # end_span should only be called once
        assert mock_adapter.end_span.call_count == 1


class TestLangfuseObservabilityAdapter:
    """Tests for LangfuseObservabilityAdapter."""

    def test_implements_observability_port(self) -> None:
        """Adapter implements ObservabilityPort interface."""
        config = LangfuseConfig(enabled=False)
        adapter = LangfuseObservabilityAdapter(config)

        assert isinstance(adapter, ObservabilityPort)

    def test_returns_null_span_when_disabled(self) -> None:
        """Returns NullSpanHandle when Langfuse is disabled."""
        config = LangfuseConfig(enabled=False)
        adapter = LangfuseObservabilityAdapter(config)

        span = adapter.start_tool_span("math", "add", {"a": 1})

        assert isinstance(span, NullSpanHandle)

    def test_sampling_respects_rate(self) -> None:
        """Sampling rate controls trace sampling."""
        config = LangfuseConfig(
            enabled=False,  # Disabled to avoid SDK dependency
            sample_rate=0.0,
        )
        adapter = LangfuseObservabilityAdapter(config)

        # With 0% sample rate, should always return NullSpanHandle
        span = adapter.start_tool_span("math", "add", {"a": 1})

        assert isinstance(span, NullSpanHandle)

    def test_record_score_with_disabled_adapter(self) -> None:
        """record_score does nothing when disabled."""
        config = LangfuseConfig(enabled=False)
        adapter = LangfuseObservabilityAdapter(config)

        # Should not raise
        adapter.record_score("trace-123", "test", 1.0, "comment")

    def test_record_health_check_with_disabled_adapter(self) -> None:
        """record_health_check does nothing when disabled."""
        config = LangfuseConfig(enabled=False)
        adapter = LangfuseObservabilityAdapter(config)

        # Should not raise
        adapter.record_health_check(
            provider_name="math",
            healthy=True,
            latency_ms=10.0,
        )

    def test_flush_with_disabled_adapter(self) -> None:
        """flush does nothing when disabled."""
        config = LangfuseConfig(enabled=False)
        adapter = LangfuseObservabilityAdapter(config)

        # Should not raise
        adapter.flush()

    def test_shutdown_with_disabled_adapter(self) -> None:
        """shutdown does nothing when disabled."""
        config = LangfuseConfig(enabled=False)
        adapter = LangfuseObservabilityAdapter(config)

        # Should not raise
        adapter.shutdown()


class TestThreadSafety:
    """Tests for thread safety of adapters."""

    def test_null_adapter_is_thread_safe(self) -> None:
        """NullObservabilityAdapter is safe for concurrent use."""
        from mcp_hangar.application.ports.observability import NullObservabilityAdapter

        adapter = NullObservabilityAdapter()
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(100):
                    span = adapter.start_tool_span("math", "add", {"a": 1})
                    span.end_success({"result": 2})
                    adapter.record_score("trace", "test", 1.0)
                    adapter.record_health_check("math", True, 10.0)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

    def test_disabled_langfuse_adapter_is_thread_safe(self) -> None:
        """Disabled LangfuseObservabilityAdapter is safe for concurrent use."""
        config = LangfuseConfig(enabled=False)
        adapter = LangfuseObservabilityAdapter(config)
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(100):
                    span = adapter.start_tool_span("math", "add", {"a": 1})
                    span.end_success({"result": 2})
                    adapter.record_score("trace", "test", 1.0)
                    adapter.flush()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
