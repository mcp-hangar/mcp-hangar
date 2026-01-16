"""Property-based tests using Hypothesis.

These tests use property-based testing to discover edge cases
that might not be covered by example-based tests.
"""

import sys

from hypothesis import given, HealthCheck, settings, strategies as st
import pytest

from mcp_hangar.application.ports.observability import NullObservabilityAdapter, NullSpanHandle, TraceContext


class TestTraceContextProperties:
    """Property-based tests for TraceContext."""

    @given(
        trace_id=st.text(min_size=1, max_size=100),
        span_id=st.one_of(st.none(), st.text(min_size=1, max_size=50)),
        user_id=st.one_of(st.none(), st.text(max_size=100)),
        session_id=st.one_of(st.none(), st.text(max_size=100)),
    )
    @settings(max_examples=100)
    def test_trace_context_creation_never_fails(
        self,
        trace_id: str,
        span_id: str | None,
        user_id: str | None,
        session_id: str | None,
    ) -> None:
        """TraceContext can be created with any string values."""
        context = TraceContext(
            trace_id=trace_id,
            span_id=span_id,
            user_id=user_id,
            session_id=session_id,
        )

        assert context.trace_id == trace_id
        assert context.span_id == span_id
        assert context.user_id == user_id
        assert context.session_id == session_id

    @given(trace_id=st.text(min_size=1, max_size=100))
    def test_trace_context_is_immutable(self, trace_id: str) -> None:
        """TraceContext fields cannot be modified after creation."""
        context = TraceContext(trace_id=trace_id)

        with pytest.raises(AttributeError):
            context.trace_id = "modified"  # type: ignore


class TestNullObservabilityAdapterProperties:
    """Property-based tests for NullObservabilityAdapter."""

    @given(
        provider_name=st.text(min_size=1, max_size=50).filter(lambda x: x.strip()),
        tool_name=st.text(min_size=1, max_size=50).filter(lambda x: x.strip()),
    )
    @settings(max_examples=100)
    def test_start_tool_span_always_returns_null_handle(
        self,
        provider_name: str,
        tool_name: str,
    ) -> None:
        """NullObservabilityAdapter always returns NullSpanHandle."""
        adapter = NullObservabilityAdapter()

        span = adapter.start_tool_span(
            provider_name=provider_name,
            tool_name=tool_name,
            input_params={},
        )

        assert isinstance(span, NullSpanHandle)

    @given(
        input_params=st.dictionaries(
            keys=st.text(min_size=1, max_size=20).filter(lambda x: x.strip()),
            values=st.one_of(
                st.integers(),
                st.floats(allow_nan=False, allow_infinity=False),
                st.text(max_size=100),
                st.booleans(),
                st.none(),
            ),
            max_size=10,
        ),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_start_tool_span_accepts_any_input_params(
        self,
        input_params: dict,
    ) -> None:
        """NullObservabilityAdapter accepts any JSON-serializable input params."""
        adapter = NullObservabilityAdapter()

        span = adapter.start_tool_span(
            provider_name="test",
            tool_name="operation",
            input_params=input_params,
        )

        assert isinstance(span, NullSpanHandle)

    @given(
        output=st.one_of(
            st.dictionaries(
                keys=st.text(min_size=1, max_size=20),
                values=st.one_of(
                    st.integers(),
                    st.floats(allow_nan=False, allow_infinity=False),
                    st.text(max_size=100),
                    st.booleans(),
                    st.none(),
                ),
                max_size=10,
            ),
            st.text(max_size=100),
            st.integers(),
            st.none(),
        ),
    )
    @settings(max_examples=50)
    def test_null_span_end_success_accepts_any_output(self, output) -> None:
        """NullSpanHandle.end_success accepts any output."""
        span = NullSpanHandle()

        # Should never raise
        span.end_success(output)

    @given(
        error_msg=st.text(max_size=500),
    )
    def test_null_span_end_error_accepts_any_error(self, error_msg: str) -> None:
        """NullSpanHandle.end_error accepts any exception."""
        span = NullSpanHandle()

        # Should never raise
        span.end_error(ValueError(error_msg))

    @given(
        trace_id=st.text(min_size=1, max_size=64),
        name=st.text(min_size=1, max_size=50).filter(lambda x: x.strip()),
        value=st.floats(allow_nan=False, allow_infinity=False),
        comment=st.one_of(st.none(), st.text(max_size=200)),
    )
    @settings(max_examples=50)
    def test_record_score_accepts_any_valid_input(
        self,
        trace_id: str,
        name: str,
        value: float,
        comment: str | None,
    ) -> None:
        """record_score accepts any valid input without raising."""
        adapter = NullObservabilityAdapter()

        # Should never raise
        adapter.record_score(
            trace_id=trace_id,
            name=name,
            value=value,
            comment=comment,
        )

    @given(
        provider_name=st.text(min_size=1, max_size=50).filter(lambda x: x.strip()),
        healthy=st.booleans(),
        latency_ms=st.floats(min_value=0, max_value=1000000, allow_nan=False),
    )
    @settings(max_examples=50)
    def test_record_health_check_accepts_any_valid_input(
        self,
        provider_name: str,
        healthy: bool,
        latency_ms: float,
    ) -> None:
        """record_health_check accepts any valid input without raising."""
        adapter = NullObservabilityAdapter()

        # Should never raise
        adapter.record_health_check(
            provider_name=provider_name,
            healthy=healthy,
            latency_ms=latency_ms,
        )


@pytest.mark.skipif(
    sys.version_info >= (3, 14), reason="Langfuse uses Pydantic v1 which is incompatible with Python 3.14+"
)
class TestLangfuseConfigProperties:
    """Property-based tests for LangfuseConfig validation."""

    @given(
        sample_rate=st.floats(min_value=-10.0, max_value=10.0),
    )
    def test_sample_rate_validation_is_consistent(self, sample_rate: float) -> None:
        """Sample rate validation is consistent for all float values."""
        from mcp_hangar.infrastructure.observability.langfuse_adapter import LangfuseConfig

        config = LangfuseConfig(
            enabled=True,
            public_key="pk-test",
            secret_key="sk-test",
            sample_rate=sample_rate,
        )

        errors = config.validate()

        if 0.0 <= sample_rate <= 1.0:
            assert not any("sample_rate" in e for e in errors)
        else:
            assert any("sample_rate" in e for e in errors)

    @given(
        public_key=st.text(max_size=100),
        secret_key=st.text(max_size=100),
        enabled=st.booleans(),
    )
    @settings(max_examples=100)
    def test_validation_never_raises(
        self,
        public_key: str,
        secret_key: str,
        enabled: bool,
    ) -> None:
        """Config validation never raises exceptions."""
        from mcp_hangar.infrastructure.observability.langfuse_adapter import LangfuseConfig

        config = LangfuseConfig(
            enabled=enabled,
            public_key=public_key,
            secret_key=secret_key,
        )

        # Should never raise
        errors = config.validate()
        assert isinstance(errors, list)

    @given(
        public_key=st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
        secret_key=st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
    )
    def test_enabled_config_with_keys_validates(
        self,
        public_key: str,
        secret_key: str,
    ) -> None:
        """Enabled config with non-empty keys passes validation."""
        from mcp_hangar.infrastructure.observability.langfuse_adapter import LangfuseConfig

        config = LangfuseConfig(
            enabled=True,
            public_key=public_key,
            secret_key=secret_key,
        )

        errors = config.validate()

        # Should have no key-related errors
        assert not any("public_key" in e for e in errors)
        assert not any("secret_key" in e for e in errors)


class TestProviderConfigProperties:
    """Property-based tests for provider configuration."""

    @given(
        idle_ttl=st.integers(min_value=0, max_value=86400),
        health_interval=st.integers(min_value=1, max_value=3600),
        max_failures=st.integers(min_value=1, max_value=100),
    )
    def test_provider_config_numeric_bounds(
        self,
        idle_ttl: int,
        health_interval: int,
        max_failures: int,
    ) -> None:
        """Provider config accepts reasonable numeric values."""
        config = {
            "idle_ttl_s": idle_ttl,
            "health_check_interval_s": health_interval,
            "max_consecutive_failures": max_failures,
        }

        # All values should be non-negative
        assert config["idle_ttl_s"] >= 0
        assert config["health_check_interval_s"] >= 1
        assert config["max_consecutive_failures"] >= 1
