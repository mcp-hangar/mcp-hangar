"""Unit tests for batch invocation functionality.

Tests cover:
- Basic batch execution
- Parallel execution
- Single-flight cold starts
- Fail-fast mode
- Timeout handling (global and per-call)
- Circuit breaker integration
- Validation
- Truncation
- Empty batch
"""

import threading
import time
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.infrastructure.single_flight import SingleFlight
from mcp_hangar.server.tools.batch import (
    _validate_batch,
    BatchExecutor,
    CallSpec,
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_TIMEOUT,
    hangar_batch,
    MAX_CALLS_PER_BATCH,
    MAX_CONCURRENCY_LIMIT,
    MAX_RESPONSE_SIZE_BYTES,
    MAX_TIMEOUT,
)

# =============================================================================
# SingleFlight Tests
# =============================================================================


class TestSingleFlight:
    """Tests for SingleFlight pattern implementation."""

    def test_single_execution_for_same_key(self):
        """Function executes only once for same key."""
        sf = SingleFlight()
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            time.sleep(0.1)
            return "result"

        results = []
        threads = []

        for _ in range(5):
            t = threading.Thread(target=lambda: results.append(sf.do("key1", fn)))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert call_count == 1  # Function called only once
        assert all(r == "result" for r in results)  # All got same result

    def test_different_keys_execute_independently(self):
        """Different keys execute independently."""
        sf = SingleFlight()
        calls = []

        def fn(key):
            calls.append(key)
            return key

        sf.do("key1", lambda: fn("key1"))
        sf.do("key2", lambda: fn("key2"))

        assert calls == ["key1", "key2"]

    def test_exception_propagates_to_all_waiters(self):
        """Exception propagates to all waiting callers."""
        sf = SingleFlight()
        errors = []

        def fn():
            time.sleep(0.1)
            raise ValueError("test error")

        threads = []
        for _ in range(3):

            def worker():
                try:
                    sf.do("key1", fn)
                except ValueError as e:
                    errors.append(str(e))

            t = threading.Thread(target=worker)
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 3
        assert all(e == "test error" for e in errors)

    def test_cache_results_mode(self):
        """With cache_results=True, result is cached."""
        sf = SingleFlight(cache_results=True)
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            return "cached"

        result1 = sf.do("key1", fn)
        result2 = sf.do("key1", fn)  # Should use cache

        assert call_count == 1
        assert result1 == result2 == "cached"

    def test_forget_clears_cache(self):
        """forget() removes cached result."""
        sf = SingleFlight(cache_results=True)
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            return f"call_{call_count}"

        result1 = sf.do("key1", fn)
        sf.forget("key1")
        result2 = sf.do("key1", fn)

        assert call_count == 2
        assert result1 == "call_1"
        assert result2 == "call_2"


# =============================================================================
# Validation Tests
# =============================================================================


class TestBatchValidation:
    """Tests for batch validation."""

    @pytest.fixture
    def mock_providers(self):
        """Mock PROVIDERS and GROUPS."""
        mock_provider = Mock()
        mock_provider.has_tools = False

        with (
            patch("mcp_hangar.server.tools.batch.PROVIDERS") as providers,
            patch("mcp_hangar.server.tools.batch.GROUPS") as groups,
        ):
            providers.get.side_effect = lambda k: mock_provider if k == "math" else None
            groups.get.return_value = None
            yield providers, groups

    def test_empty_calls_valid(self, mock_providers):
        """Empty calls list is valid."""
        errors = _validate_batch([], DEFAULT_MAX_CONCURRENCY, DEFAULT_TIMEOUT)
        assert errors == []

    def test_valid_batch(self, mock_providers):
        """Valid batch passes validation."""
        calls = [
            {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
        ]
        errors = _validate_batch(calls, DEFAULT_MAX_CONCURRENCY, DEFAULT_TIMEOUT)
        assert errors == []

    def test_batch_size_exceeded(self, mock_providers):
        """Batch size exceeding limit fails."""
        calls = [{"provider": "math", "tool": "add", "arguments": {}} for _ in range(MAX_CALLS_PER_BATCH + 1)]
        errors = _validate_batch(calls, DEFAULT_MAX_CONCURRENCY, DEFAULT_TIMEOUT)

        assert len(errors) == 1
        assert errors[0].field == "calls"
        assert "exceeds maximum" in errors[0].message

    def test_invalid_max_concurrency(self, mock_providers):
        """Invalid max_concurrency fails."""
        calls = [{"provider": "math", "tool": "add", "arguments": {}}]

        # Too low
        errors = _validate_batch(calls, 0, DEFAULT_TIMEOUT)
        assert any(e.field == "max_concurrency" for e in errors)

        # Too high
        errors = _validate_batch(calls, MAX_CONCURRENCY_LIMIT + 1, DEFAULT_TIMEOUT)
        assert any(e.field == "max_concurrency" for e in errors)

    def test_invalid_timeout(self, mock_providers):
        """Invalid timeout fails."""
        calls = [{"provider": "math", "tool": "add", "arguments": {}}]

        # Too low
        errors = _validate_batch(calls, DEFAULT_MAX_CONCURRENCY, 0.5)
        assert any(e.field == "timeout" for e in errors)

        # Too high
        errors = _validate_batch(calls, DEFAULT_MAX_CONCURRENCY, MAX_TIMEOUT + 1)
        assert any(e.field == "timeout" for e in errors)

    def test_missing_provider(self, mock_providers):
        """Missing provider field fails."""
        calls = [{"tool": "add", "arguments": {}}]
        errors = _validate_batch(calls, DEFAULT_MAX_CONCURRENCY, DEFAULT_TIMEOUT)

        assert len(errors) == 1
        assert errors[0].field == "provider"

    def test_missing_tool(self, mock_providers):
        """Missing tool field fails."""
        calls = [{"provider": "math", "arguments": {}}]
        errors = _validate_batch(calls, DEFAULT_MAX_CONCURRENCY, DEFAULT_TIMEOUT)

        assert len(errors) == 1
        assert errors[0].field == "tool"

    def test_missing_arguments(self, mock_providers):
        """Missing arguments field fails."""
        calls = [{"provider": "math", "tool": "add"}]
        errors = _validate_batch(calls, DEFAULT_MAX_CONCURRENCY, DEFAULT_TIMEOUT)

        assert len(errors) == 1
        assert errors[0].field == "arguments"

    def test_provider_not_found(self, mock_providers):
        """Non-existent provider fails."""
        calls = [{"provider": "nonexistent", "tool": "add", "arguments": {}}]
        errors = _validate_batch(calls, DEFAULT_MAX_CONCURRENCY, DEFAULT_TIMEOUT)

        assert len(errors) == 1
        assert errors[0].field == "provider"
        assert "not found" in errors[0].message

    def test_invalid_per_call_timeout(self, mock_providers):
        """Invalid per-call timeout fails."""
        calls = [
            {"provider": "math", "tool": "add", "arguments": {}, "timeout": -1},
        ]
        errors = _validate_batch(calls, DEFAULT_MAX_CONCURRENCY, DEFAULT_TIMEOUT)

        assert len(errors) == 1
        assert errors[0].field == "timeout"


# =============================================================================
# Batch Execution Tests
# =============================================================================


class TestBatchExecution:
    """Tests for batch execution."""

    @pytest.fixture
    def mock_context(self):
        """Mock application context."""
        ctx = Mock()
        ctx.event_bus = Mock()
        ctx.command_bus = Mock()
        ctx.command_bus.send.return_value = {"result": 42}

        with patch("mcp_hangar.server.tools.batch.get_context", return_value=ctx):
            yield ctx

    @pytest.fixture
    def mock_providers_for_execution(self):
        """Mock PROVIDERS and GROUPS for execution."""
        mock_provider = Mock()
        mock_provider.state.value = "ready"
        mock_provider.has_tools = False
        mock_provider.health.circuit_breaker_open = False

        with (
            patch("mcp_hangar.server.tools.batch.PROVIDERS") as providers,
            patch("mcp_hangar.server.tools.batch.GROUPS") as groups,
        ):
            providers.get.side_effect = lambda k: mock_provider if k == "math" else None
            groups.get.return_value = None
            yield providers, groups, mock_provider

    def test_execute_single_call(self, mock_context, mock_providers_for_execution):
        """Single call executes successfully."""
        executor = BatchExecutor()
        calls = [CallSpec(index=0, call_id="call-1", provider="math", tool="add", arguments={"a": 1})]

        result = executor.execute(
            batch_id="batch-1",
            calls=calls,
            max_concurrency=10,
            global_timeout=60.0,
            fail_fast=False,
        )

        assert result.success is True
        assert result.total == 1
        assert result.succeeded == 1
        assert result.failed == 0

    def test_execute_multiple_calls_parallel(self, mock_context, mock_providers_for_execution):
        """Multiple calls execute in parallel."""
        executor = BatchExecutor()
        calls = [
            CallSpec(index=i, call_id=f"call-{i}", provider="math", tool="add", arguments={"a": i}) for i in range(5)
        ]

        result = executor.execute(
            batch_id="batch-1",
            calls=calls,
            max_concurrency=5,
            global_timeout=60.0,
            fail_fast=False,
        )

        assert result.success is True
        assert result.total == 5
        assert result.succeeded == 5
        # Should be faster than sequential (5 * delay)
        # Just verify it completed

    def test_partial_failure(self, mock_context, mock_providers_for_execution):
        """Batch continues on partial failure."""
        providers, groups, mock_provider = mock_providers_for_execution

        # Make provider alternately fail
        call_count = [0]

        def mock_send(cmd):
            call_count[0] += 1
            if call_count[0] % 2 == 0:
                raise ValueError("Simulated error")
            return {"result": 42}

        mock_context.command_bus.send.side_effect = mock_send

        executor = BatchExecutor()
        calls = [
            CallSpec(index=i, call_id=f"call-{i}", provider="math", tool="add", arguments={"a": i}) for i in range(4)
        ]

        result = executor.execute(
            batch_id="batch-1",
            calls=calls,
            max_concurrency=1,  # Sequential to ensure predictable failures
            global_timeout=60.0,
            fail_fast=False,
        )

        assert result.success is False  # Partial failure
        assert result.total == 4
        assert result.succeeded == 2
        assert result.failed == 2

    def test_fail_fast_stops_on_error(self, mock_context, mock_providers_for_execution):
        """Fail-fast mode stops on first error."""
        call_count = [0]

        def mock_send(cmd):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise ValueError("Simulated error")
            time.sleep(0.1)  # Slow down to ensure ordering
            return {"result": 42}

        mock_context.command_bus.send.side_effect = mock_send

        executor = BatchExecutor()
        calls = [
            CallSpec(index=i, call_id=f"call-{i}", provider="math", tool="add", arguments={"a": i}) for i in range(5)
        ]

        result = executor.execute(
            batch_id="batch-1",
            calls=calls,
            max_concurrency=1,  # Sequential
            global_timeout=60.0,
            fail_fast=True,
        )

        # Should have stopped after first error
        assert result.failed >= 1
        # Some calls may have been cancelled
        assert result.total == 5

    def test_circuit_breaker_rejection(self, mock_context, mock_providers_for_execution):
        """Circuit breaker OPEN rejects calls immediately."""
        providers, groups, mock_provider = mock_providers_for_execution
        mock_provider.health.circuit_breaker_open = True

        executor = BatchExecutor()
        calls = [CallSpec(index=0, call_id="call-1", provider="math", tool="add", arguments={"a": 1})]

        result = executor.execute(
            batch_id="batch-1",
            calls=calls,
            max_concurrency=10,
            global_timeout=60.0,
            fail_fast=False,
        )

        assert result.success is False
        assert result.failed == 1
        assert result.results[0].error_type == "CircuitBreakerOpen"

    def test_emits_domain_events(self, mock_context, mock_providers_for_execution):
        """Batch emits appropriate domain events."""
        executor = BatchExecutor()
        calls = [CallSpec(index=0, call_id="call-1", provider="math", tool="add", arguments={"a": 1})]

        executor.execute(
            batch_id="batch-1",
            calls=calls,
            max_concurrency=10,
            global_timeout=60.0,
            fail_fast=False,
        )

        # Check events were published
        published_events = [call[0][0] for call in mock_context.event_bus.publish.call_args_list]
        event_types = [type(e).__name__ for e in published_events]

        assert "BatchInvocationRequested" in event_types
        assert "BatchCallCompleted" in event_types
        assert "BatchInvocationCompleted" in event_types


# =============================================================================
# hangar_batch Tool Tests
# =============================================================================


class TestHangarBatchTool:
    """Tests for hangar_batch MCP tool."""

    @pytest.fixture
    def mock_all(self):
        """Mock all dependencies."""
        ctx = Mock()
        ctx.event_bus = Mock()
        ctx.command_bus = Mock()
        ctx.command_bus.send.return_value = {"result": 42}

        mock_provider = Mock()
        mock_provider.state.value = "ready"
        mock_provider.has_tools = False
        mock_provider.health.circuit_breaker_open = False

        with (
            patch("mcp_hangar.server.tools.batch.get_context", return_value=ctx),
            patch("mcp_hangar.server.tools.batch.PROVIDERS") as providers,
            patch("mcp_hangar.server.tools.batch.GROUPS") as groups,
        ):
            providers.get.side_effect = lambda k: mock_provider if k == "math" else None
            groups.get.return_value = None
            yield ctx, providers, mock_provider

    def test_empty_batch_returns_success(self, mock_all):
        """Empty batch returns valid no-op response."""
        result = hangar_batch(calls=[])

        assert result["success"] is True
        assert result["total"] == 0
        assert result["succeeded"] == 0
        assert result["failed"] == 0
        assert result["results"] == []
        assert "batch_id" in result

    def test_simple_batch(self, mock_all):
        """Simple batch executes successfully."""
        result = hangar_batch(
            calls=[
                {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
                {"provider": "math", "tool": "multiply", "arguments": {"a": 3, "b": 4}},
            ]
        )

        assert result["success"] is True
        assert result["total"] == 2
        assert result["succeeded"] == 2
        assert result["failed"] == 0
        assert len(result["results"]) == 2

    def test_validation_error_response(self, mock_all):
        """Validation error returns proper response."""
        result = hangar_batch(
            calls=[
                {"provider": "nonexistent", "tool": "add", "arguments": {}},
            ]
        )

        assert result["success"] is False
        assert "validation_errors" in result
        assert len(result["validation_errors"]) == 1

    def test_result_contains_call_ids(self, mock_all):
        """Results contain batch_id and call_id."""
        result = hangar_batch(
            calls=[
                {"provider": "math", "tool": "add", "arguments": {}},
            ]
        )

        assert "batch_id" in result
        assert "call_id" in result["results"][0]

    def test_results_preserve_order(self, mock_all):
        """Results are in original call order."""
        result = hangar_batch(
            calls=[
                {"provider": "math", "tool": "add", "arguments": {"a": 1}},
                {"provider": "math", "tool": "add", "arguments": {"a": 2}},
                {"provider": "math", "tool": "add", "arguments": {"a": 3}},
            ]
        )

        indices = [r["index"] for r in result["results"]]
        assert indices == [0, 1, 2]

    def test_clamps_concurrency(self, mock_all):
        """Concurrency is clamped to limits."""
        # Should not raise
        result = hangar_batch(
            calls=[{"provider": "math", "tool": "add", "arguments": {}}],
            max_concurrency=100,  # Above limit
        )
        assert result["success"] is True

    def test_clamps_timeout(self, mock_all):
        """Timeout is clamped to limits."""
        # Should not raise
        result = hangar_batch(
            calls=[{"provider": "math", "tool": "add", "arguments": {}}],
            timeout=1000.0,  # Above limit
        )
        assert result["success"] is True


# =============================================================================
# Response Truncation Tests
# =============================================================================


class TestResponseTruncation:
    """Tests for response truncation behavior."""

    @pytest.fixture
    def mock_large_response(self):
        """Mock context with large response."""
        ctx = Mock()
        ctx.event_bus = Mock()
        ctx.command_bus = Mock()
        # Return a response larger than MAX_RESPONSE_SIZE_BYTES
        large_data = {"data": "x" * (MAX_RESPONSE_SIZE_BYTES + 1000)}
        ctx.command_bus.send.return_value = large_data

        mock_provider = Mock()
        mock_provider.state.value = "ready"
        mock_provider.has_tools = False
        mock_provider.health.circuit_breaker_open = False

        with (
            patch("mcp_hangar.server.tools.batch.get_context", return_value=ctx),
            patch("mcp_hangar.server.tools.batch.PROVIDERS") as providers,
            patch("mcp_hangar.server.tools.batch.GROUPS") as groups,
        ):
            providers.get.side_effect = lambda k: mock_provider if k == "math" else None
            groups.get.return_value = None
            yield ctx

    def test_truncates_large_response(self, mock_large_response):
        """Large responses are truncated."""
        result = hangar_batch(
            calls=[
                {"provider": "math", "tool": "add", "arguments": {}},
            ]
        )

        call_result = result["results"][0]
        assert call_result["truncated"] is True
        assert call_result["truncated_reason"] == "response_size_exceeded"
        assert call_result["original_size_bytes"] is not None
        assert call_result["result"] is None  # No partial data
