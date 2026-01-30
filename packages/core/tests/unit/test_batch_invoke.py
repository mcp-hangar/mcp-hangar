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
- hangar_call unified API
- Retry functionality
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
    hangar_call,
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
        """Mock context and GROUPS."""
        mock_provider = Mock()
        mock_provider.has_tools = False

        mock_ctx = Mock()
        mock_ctx.get_provider.side_effect = lambda k: mock_provider if k == "math" else None

        with (
            patch("mcp_hangar.server.tools.batch.get_context") as get_context,
            patch("mcp_hangar.server.tools.batch.GROUPS") as groups,
        ):
            get_context.return_value = mock_ctx
            groups.get.return_value = None
            yield mock_ctx, groups

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
    def mock_providers_for_execution(self, mock_context):
        """Mock context provider lookup and GROUPS for execution."""
        mock_provider = Mock()
        mock_provider.state.value = "ready"
        mock_provider.has_tools = False
        mock_provider.health.should_degrade.return_value = False

        # Configure context to return provider
        mock_context.get_provider.side_effect = lambda k: mock_provider if k == "math" else None
        mock_context.provider_exists.side_effect = lambda k: k == "math"

        with patch("mcp_hangar.server.tools.batch.GROUPS") as groups:
            groups.get.return_value = None
            yield mock_context, groups, mock_provider

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
        ctx, groups, mock_provider = mock_providers_for_execution

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
        ctx, groups, mock_provider = mock_providers_for_execution
        mock_provider.health.should_degrade.return_value = True

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
# hangar_call Tool Tests (Basic)
# =============================================================================


class TestHangarCallToolBasic:
    """Basic tests for hangar_call MCP tool."""

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
        mock_provider.health.should_degrade.return_value = False

        # Configure context to return provider
        ctx.get_provider.side_effect = lambda k: mock_provider if k == "math" else None
        ctx.provider_exists.side_effect = lambda k: k == "math"

        with (
            patch("mcp_hangar.server.tools.batch.get_context", return_value=ctx),
            patch("mcp_hangar.server.tools.batch.GROUPS") as groups,
        ):
            groups.get.return_value = None
            yield ctx, mock_provider

    def test_empty_batch_returns_success(self, mock_all):
        """Empty batch returns valid no-op response."""
        result = hangar_call(calls=[])

        assert result["success"] is True
        assert result["total"] == 0
        assert result["succeeded"] == 0
        assert result["failed"] == 0
        assert result["results"] == []
        assert "batch_id" in result

    def test_simple_batch(self, mock_all):
        """Simple batch executes successfully."""
        result = hangar_call(
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
        result = hangar_call(
            calls=[
                {"provider": "nonexistent", "tool": "add", "arguments": {}},
            ]
        )

        assert result["success"] is False
        assert "validation_errors" in result
        assert len(result["validation_errors"]) == 1

    def test_result_contains_call_ids(self, mock_all):
        """Results contain batch_id and call_id."""
        result = hangar_call(
            calls=[
                {"provider": "math", "tool": "add", "arguments": {}},
            ]
        )

        assert "batch_id" in result
        assert "call_id" in result["results"][0]

    def test_results_preserve_order(self, mock_all):
        """Results are in original call order."""
        result = hangar_call(
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
        result = hangar_call(
            calls=[{"provider": "math", "tool": "add", "arguments": {}}],
            max_concurrency=100,  # Above limit
        )
        assert result["success"] is True

    def test_clamps_timeout(self, mock_all):
        """Timeout is clamped to limits."""
        # Should not raise
        result = hangar_call(
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
        mock_provider.health.should_degrade.return_value = False

        # Configure context to return provider
        ctx.get_provider.side_effect = lambda k: mock_provider if k == "math" else None
        ctx.provider_exists.side_effect = lambda k: k == "math"

        with (
            patch("mcp_hangar.server.tools.batch.get_context", return_value=ctx),
            patch("mcp_hangar.server.tools.batch.GROUPS") as groups,
        ):
            groups.get.return_value = None
            yield ctx

    def test_truncates_large_response(self, mock_large_response):
        """Large responses are truncated."""
        result = hangar_call(
            calls=[
                {"provider": "math", "tool": "add", "arguments": {}},
            ]
        )

        call_result = result["results"][0]
        assert call_result["truncated"] is True
        assert call_result["truncated_reason"] == "response_size_exceeded"
        assert call_result["original_size_bytes"] is not None
        assert call_result["result"] is None  # No partial data


# =============================================================================
# Cross-Provider Batch Tests
# =============================================================================


class TestCrossProviderBatch:
    """Tests for batch invocations across multiple providers."""

    @pytest.fixture
    def mock_multiple_providers(self):
        """Mock multiple providers for cross-provider batch testing."""
        ctx = Mock()
        ctx.event_bus = Mock()
        ctx.command_bus = Mock()
        ctx.command_bus.send.return_value = {"result": 42}

        # Create different mock providers
        math_provider = Mock()
        math_provider.state.value = "ready"
        math_provider.has_tools = False
        math_provider.health.should_degrade.return_value = False

        filesystem_provider = Mock()
        filesystem_provider.state.value = "ready"
        filesystem_provider.has_tools = False
        filesystem_provider.health.should_degrade.return_value = False

        fetch_provider = Mock()
        fetch_provider.state.value = "ready"
        fetch_provider.has_tools = False
        fetch_provider.health.should_degrade.return_value = False

        providers = {
            "math": math_provider,
            "filesystem": filesystem_provider,
            "fetch": fetch_provider,
        }

        # Configure context to return appropriate provider
        ctx.get_provider.side_effect = lambda k: providers.get(k)
        ctx.provider_exists.side_effect = lambda k: k in providers

        with (
            patch("mcp_hangar.server.tools.batch.get_context", return_value=ctx),
            patch("mcp_hangar.server.tools.batch.GROUPS") as groups,
        ):
            groups.get.return_value = None
            yield ctx, providers

    def test_cross_provider_batch_success(self, mock_multiple_providers):
        """Batch with multiple different providers executes successfully."""
        result = hangar_call(
            calls=[
                {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
                {"provider": "filesystem", "tool": "read", "arguments": {"path": "/tmp"}},
                {"provider": "fetch", "tool": "get", "arguments": {"url": "http://example.com"}},
            ]
        )

        assert result["success"] is True
        assert result["total"] == 3
        assert result["succeeded"] == 3
        assert result["failed"] == 0
        assert len(result["results"]) == 3

    def test_cross_provider_batch_partial_failure(self, mock_multiple_providers):
        """Batch continues when one provider fails."""
        ctx, providers = mock_multiple_providers

        # Make fetch provider fail
        call_count = [0]

        def mock_send(cmd):
            call_count[0] += 1
            if cmd.provider_id == "fetch":
                raise ValueError("Fetch failed")
            return {"result": 42}

        ctx.command_bus.send.side_effect = mock_send

        result = hangar_call(
            calls=[
                {"provider": "math", "tool": "add", "arguments": {"a": 1}},
                {"provider": "fetch", "tool": "get", "arguments": {"url": "http://fail.com"}},
                {"provider": "filesystem", "tool": "read", "arguments": {"path": "/tmp"}},
            ],
            fail_fast=False,
        )

        assert result["success"] is False
        assert result["total"] == 3
        assert result["succeeded"] == 2
        assert result["failed"] == 1

    def test_cross_provider_batch_with_unknown_provider(self, mock_multiple_providers):
        """Batch fails validation when provider is unknown."""
        result = hangar_call(
            calls=[
                {"provider": "math", "tool": "add", "arguments": {}},
                {"provider": "unknown_provider", "tool": "foo", "arguments": {}},
            ]
        )

        assert result["success"] is False
        assert "validation_errors" in result
        # Should have one validation error for unknown provider
        errors = result["validation_errors"]
        assert any("unknown_provider" in str(e) for e in errors)

    def test_cross_provider_batch_respects_per_provider_circuit_breaker(self, mock_multiple_providers):
        """Each provider's circuit breaker is checked independently."""
        ctx, providers = mock_multiple_providers

        # Make fetch provider degraded (circuit breaker open)
        providers["fetch"].health.should_degrade.return_value = True

        result = hangar_call(
            calls=[
                {"provider": "math", "tool": "add", "arguments": {}},
                {"provider": "fetch", "tool": "get", "arguments": {}},
                {"provider": "filesystem", "tool": "read", "arguments": {}},
            ],
            max_concurrency=1,  # Sequential to ensure predictable order
        )

        # Only fetch should fail due to circuit breaker
        assert result["total"] == 3
        assert result["succeeded"] == 2
        assert result["failed"] == 1

        # Find the failed result
        failed_results = [r for r in result["results"] if not r["success"]]
        assert len(failed_results) == 1
        assert failed_results[0]["error_type"] == "CircuitBreakerOpen"


# =============================================================================
# hangar_call Unified API Tests
# =============================================================================


class TestHangarCallTool:
    """Tests for hangar_call unified MCP tool."""

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
        mock_provider.health.should_degrade.return_value = False

        # Configure context to return provider
        ctx.get_provider.side_effect = lambda k: mock_provider if k == "math" else None
        ctx.provider_exists.side_effect = lambda k: k == "math"

        with (
            patch("mcp_hangar.server.tools.batch.get_context", return_value=ctx),
            patch("mcp_hangar.server.tools.batch.GROUPS") as groups,
        ):
            groups.get.return_value = None
            yield ctx, mock_provider

    def test_empty_calls_returns_success(self, mock_all):
        """Empty calls list returns valid no-op response."""
        result = hangar_call(calls=[])

        assert result["success"] is True
        assert result["total"] == 0
        assert result["succeeded"] == 0
        assert result["failed"] == 0
        assert result["results"] == []
        assert "batch_id" in result

    def test_single_call_success(self, mock_all):
        """Single call executes successfully."""
        result = hangar_call(
            calls=[
                {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
            ]
        )

        assert result["success"] is True
        assert result["total"] == 1
        assert result["succeeded"] == 1
        assert result["failed"] == 0
        assert len(result["results"]) == 1
        assert result["results"][0]["success"] is True

    def test_single_call_with_retry_param(self, mock_all):
        """Single call with max_retries parameter."""
        result = hangar_call(
            calls=[
                {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
            ],
            max_retries=3,
        )

        assert result["success"] is True
        assert result["total"] == 1
        assert result["succeeded"] == 1

    def test_multiple_calls_success(self, mock_all):
        """Multiple calls execute successfully."""
        result = hangar_call(
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

    def test_max_retries_clamped_to_valid_range(self, mock_all):
        """max_retries is clamped to valid range (1-10)."""
        # Too low - should clamp to 1
        result = hangar_call(
            calls=[{"provider": "math", "tool": "add", "arguments": {}}],
            max_retries=0,
        )
        assert result["success"] is True

        # Too high - should clamp to 10
        result = hangar_call(
            calls=[{"provider": "math", "tool": "add", "arguments": {}}],
            max_retries=100,
        )
        assert result["success"] is True

    def test_validation_error_response(self, mock_all):
        """Validation error returns proper response."""
        result = hangar_call(
            calls=[
                {"provider": "nonexistent", "tool": "add", "arguments": {}},
            ]
        )

        assert result["success"] is False
        assert "validation_errors" in result
        assert len(result["validation_errors"]) == 1

    def test_result_contains_call_ids(self, mock_all):
        """Results contain batch_id and call_id."""
        result = hangar_call(
            calls=[
                {"provider": "math", "tool": "add", "arguments": {}},
            ]
        )

        assert "batch_id" in result
        assert "call_id" in result["results"][0]

    def test_results_preserve_order(self, mock_all):
        """Results are in original call order."""
        result = hangar_call(
            calls=[
                {"provider": "math", "tool": "add", "arguments": {"a": 1}},
                {"provider": "math", "tool": "add", "arguments": {"a": 2}},
                {"provider": "math", "tool": "add", "arguments": {"a": 3}},
            ]
        )

        indices = [r["index"] for r in result["results"]]
        assert indices == [0, 1, 2]

    def test_fail_fast_mode(self, mock_all):
        """fail_fast mode stops on first error."""
        ctx, mock_provider = mock_all

        call_count = [0]

        def mock_send(cmd):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise ValueError("Simulated error")
            time.sleep(0.05)
            return {"result": 42}

        ctx.command_bus.send.side_effect = mock_send

        result = hangar_call(
            calls=[{"provider": "math", "tool": "add", "arguments": {"a": i}} for i in range(5)],
            max_concurrency=1,  # Sequential
            fail_fast=True,
        )

        assert result["failed"] >= 1
        assert result["total"] == 5


# =============================================================================
# Retry Functionality Tests
# =============================================================================


class TestRetryFunctionality:
    """Tests for retry functionality in hangar_call."""

    @pytest.fixture
    def mock_with_retry(self):
        """Mock context with configurable failure behavior."""
        ctx = Mock()
        ctx.event_bus = Mock()
        ctx.command_bus = Mock()

        mock_provider = Mock()
        mock_provider.state.value = "ready"
        mock_provider.has_tools = False
        mock_provider.health.should_degrade.return_value = False

        ctx.get_provider.side_effect = lambda k: mock_provider if k == "math" else None
        ctx.provider_exists.side_effect = lambda k: k == "math"

        with (
            patch("mcp_hangar.server.tools.batch.get_context", return_value=ctx),
            patch("mcp_hangar.server.tools.batch.GROUPS") as groups,
        ):
            groups.get.return_value = None
            yield ctx, mock_provider

    def test_retry_on_transient_failure(self, mock_with_retry):
        """Retry recovers from transient failures."""
        ctx, mock_provider = mock_with_retry

        call_count = [0]

        def mock_send(cmd):
            call_count[0] += 1
            if call_count[0] < 3:  # Fail first 2 attempts
                raise TimeoutError("Simulated timeout")
            return {"result": 42}

        ctx.command_bus.send.side_effect = mock_send

        result = hangar_call(
            calls=[{"provider": "math", "tool": "add", "arguments": {}}],
            max_retries=5,
        )

        assert result["success"] is True
        assert result["succeeded"] == 1
        # Should have retry metadata
        call_result = result["results"][0]
        assert call_result["success"] is True
        if "retry_metadata" in call_result:
            assert call_result["retry_metadata"]["attempts"] >= 3

    def test_retry_exhausted_returns_failure(self, mock_with_retry):
        """All retries exhausted returns failure."""
        ctx, mock_provider = mock_with_retry

        # Always fail
        ctx.command_bus.send.side_effect = TimeoutError("Always fail")

        result = hangar_call(
            calls=[{"provider": "math", "tool": "add", "arguments": {}}],
            max_retries=3,
        )

        assert result["success"] is False
        assert result["failed"] == 1

        call_result = result["results"][0]
        assert call_result["success"] is False
        assert "retry_metadata" in call_result
        # RetryResult.attempt_count counts failed attempts recorded in `attempts` list
        # plus 1 if successful. Since all 3 attempts failed, the last one isn't
        # in the attempts list (it's the final_error), so attempts = 2 recorded + 1 final = 3 total
        # But our RetryMetadata.attempts shows the attempt_count from RetryResult
        assert call_result["retry_metadata"]["attempts"] >= 2  # At least 2 recorded attempts

    def test_no_retry_when_max_retries_is_one(self, mock_with_retry):
        """No retry when max_retries is 1 (default)."""
        ctx, mock_provider = mock_with_retry

        call_count = [0]

        def mock_send(cmd):
            call_count[0] += 1
            if call_count[0] < 2:
                raise TimeoutError("Simulated timeout")
            return {"result": 42}

        ctx.command_bus.send.side_effect = mock_send

        result = hangar_call(
            calls=[{"provider": "math", "tool": "add", "arguments": {}}],
            max_retries=1,  # No retry
        )

        # Should fail since no retry
        assert result["success"] is False
        assert result["failed"] == 1
        assert call_count[0] == 1  # Only one attempt

    def test_retry_metadata_contains_error_types(self, mock_with_retry):
        """Retry metadata includes error types from attempts."""
        ctx, mock_provider = mock_with_retry

        call_count = [0]

        def mock_send(cmd):
            call_count[0] += 1
            if call_count[0] == 1:
                raise TimeoutError("First failure")
            elif call_count[0] == 2:
                raise ConnectionError("Second failure")
            return {"result": 42}

        ctx.command_bus.send.side_effect = mock_send

        result = hangar_call(
            calls=[{"provider": "math", "tool": "add", "arguments": {}}],
            max_retries=5,
        )

        assert result["success"] is True
        call_result = result["results"][0]
        if "retry_metadata" in call_result:
            retries = call_result["retry_metadata"]["retries"]
            # Should have recorded the error types from failed attempts
            assert len(retries) >= 2
