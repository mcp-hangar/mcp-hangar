"""Batch invocation tool for MCP Hangar.

Executes multiple tool invocations in parallel with configurable concurrency,
timeout handling, and fail-fast behavior.

Features:
- Parallel execution with ThreadPoolExecutor
- Single-flight pattern for cold starts (one provider starts once, not N times)
- Cooperative cancellation via threading.Event
- Eager validation before execution
- Partial success handling (default: continue on error)
- Response truncation for oversized payloads
- Circuit breaker integration

Example:
    hangar_batch(calls=[
        {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
        {"provider": "math", "tool": "multiply", "arguments": {"a": 3, "b": 4}},
    ])
"""

from concurrent.futures import as_completed, ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import threading
import time
from typing import Any
import uuid

from mcp.server.fastmcp import FastMCP

from ...application.commands import InvokeToolCommand, StartProviderCommand
from ...domain.events import BatchCallCompleted, BatchInvocationCompleted, BatchInvocationRequested
from ...infrastructure.single_flight import SingleFlight
from ...logging_config import get_logger
from ...metrics import (
    BATCH_CALLS_TOTAL,
    BATCH_CANCELLATIONS_TOTAL,
    BATCH_CIRCUIT_BREAKER_REJECTIONS_TOTAL,
    BATCH_CONCURRENCY_GAUGE,
    BATCH_DURATION_SECONDS,
    BATCH_SIZE_HISTOGRAM,
    BATCH_TRUNCATIONS_TOTAL,
    BATCH_VALIDATION_FAILURES_TOTAL,
)
from ..context import get_context
from ..state import GROUPS, PROVIDERS

logger = get_logger(__name__)

# =============================================================================
# Configuration Constants
# =============================================================================

DEFAULT_MAX_CONCURRENCY = 10
MAX_CONCURRENCY_LIMIT = 20
DEFAULT_TIMEOUT = 60.0
MAX_TIMEOUT = 300.0
MAX_CALLS_PER_BATCH = 100
MAX_RESPONSE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB per call
MAX_TOTAL_RESPONSE_SIZE_BYTES = 50 * 1024 * 1024  # 50MB total

# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class CallSpec:
    """Specification for a single call within a batch."""

    index: int
    call_id: str
    provider: str
    tool: str
    arguments: dict[str, Any]
    timeout: float | None = None


@dataclass
class CallResult:
    """Result of a single call within a batch."""

    index: int
    call_id: str
    success: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    error_type: str | None = None
    elapsed_ms: float = 0.0
    truncated: bool = False
    truncated_reason: str | None = None
    original_size_bytes: int | None = None


@dataclass
class BatchResult:
    """Result of a batch invocation."""

    batch_id: str
    success: bool
    total: int
    succeeded: int
    failed: int
    elapsed_ms: float
    results: list[CallResult] = field(default_factory=list)
    cancelled: int = 0


@dataclass
class ValidationError:
    """Validation error for a single call."""

    index: int
    field: str
    message: str


# =============================================================================
# Validation
# =============================================================================


def _validate_batch(
    calls: list[dict[str, Any]],
    max_concurrency: int,
    timeout: float,
) -> list[ValidationError]:
    """Eagerly validate batch before execution.

    Validates:
    - Batch size limits
    - Concurrency and timeout bounds
    - Each call's provider exists
    - Each call's tool exists
    - Each call's arguments are valid

    Args:
        calls: List of call specifications.
        max_concurrency: Requested concurrency.
        timeout: Requested timeout.

    Returns:
        List of validation errors (empty if valid).
    """
    errors: list[ValidationError] = []

    # Validate batch-level constraints
    if len(calls) > MAX_CALLS_PER_BATCH:
        errors.append(
            ValidationError(
                index=-1,
                field="calls",
                message=f"Batch size {len(calls)} exceeds maximum {MAX_CALLS_PER_BATCH}",
            )
        )
        return errors  # Early return - batch is rejected entirely

    if max_concurrency < 1 or max_concurrency > MAX_CONCURRENCY_LIMIT:
        errors.append(
            ValidationError(
                index=-1,
                field="max_concurrency",
                message=f"max_concurrency must be between 1 and {MAX_CONCURRENCY_LIMIT}",
            )
        )

    if timeout < 1 or timeout > MAX_TIMEOUT:
        errors.append(
            ValidationError(
                index=-1,
                field="timeout",
                message=f"timeout must be between 1 and {MAX_TIMEOUT} seconds",
            )
        )

    # Validate each call
    for i, call in enumerate(calls):
        # Required fields
        if not isinstance(call, dict):
            errors.append(ValidationError(index=i, field="call", message="Call must be a dictionary"))
            continue

        provider = call.get("provider")
        if not provider or not isinstance(provider, str):
            errors.append(
                ValidationError(index=i, field="provider", message="provider is required and must be a string")
            )
            continue

        tool = call.get("tool")
        if not tool or not isinstance(tool, str):
            errors.append(ValidationError(index=i, field="tool", message="tool is required and must be a string"))
            continue

        arguments = call.get("arguments")
        if arguments is None:
            errors.append(ValidationError(index=i, field="arguments", message="arguments is required"))
            continue
        if not isinstance(arguments, dict):
            errors.append(ValidationError(index=i, field="arguments", message="arguments must be a dictionary"))
            continue

        # Provider exists (check both providers and groups)
        provider_obj = PROVIDERS.get(provider) or GROUPS.get(provider)
        if not provider_obj:
            errors.append(
                ValidationError(
                    index=i,
                    field="provider",
                    message=f"Provider '{provider}' not found",
                )
            )
            continue

        # Tool exists (if provider has predefined tools, check against them)
        # Note: For COLD providers without predefined tools, we skip tool validation
        # as tools will be discovered on start
        if hasattr(provider_obj, "has_tools") and provider_obj.has_tools:
            tool_schema = provider_obj.tools.get_tool(tool)
            if not tool_schema:
                errors.append(
                    ValidationError(
                        index=i,
                        field="tool",
                        message=f"Tool '{tool}' not found in provider '{provider}'",
                    )
                )
                continue

        # Per-call timeout validation
        call_timeout = call.get("timeout")
        if call_timeout is not None:
            if not isinstance(call_timeout, int | float) or call_timeout <= 0:
                errors.append(
                    ValidationError(
                        index=i,
                        field="timeout",
                        message="timeout must be a positive number",
                    )
                )

    return errors


# =============================================================================
# Batch Execution
# =============================================================================


class BatchExecutor:
    """Executes batch invocations with parallel processing."""

    def __init__(self):
        self._single_flight = SingleFlight(cache_results=False)
        self._active_batches = 0
        self._active_lock = threading.Lock()

    def execute(
        self,
        batch_id: str,
        calls: list[CallSpec],
        max_concurrency: int,
        global_timeout: float,
        fail_fast: bool,
    ) -> BatchResult:
        """Execute batch of calls in parallel.

        Args:
            batch_id: Unique batch identifier.
            calls: List of call specifications.
            max_concurrency: Maximum parallel workers.
            global_timeout: Global timeout for entire batch.
            fail_fast: Abort on first error if True.

        Returns:
            BatchResult with all call results.
        """
        ctx = get_context()
        start_time = time.perf_counter()
        cancel_event = threading.Event()
        results: list[CallResult | None] = [None] * len(calls)
        succeeded = 0
        failed = 0
        cancelled = 0

        # Track active batches for metrics
        with self._active_lock:
            self._active_batches += 1
            BATCH_CONCURRENCY_GAUGE.set(self._active_batches)

        try:
            # Emit batch requested event
            providers = list(set(c.provider for c in calls))
            ctx.event_bus.publish(
                BatchInvocationRequested(
                    batch_id=batch_id,
                    call_count=len(calls),
                    providers=providers,
                    max_concurrency=max_concurrency,
                    timeout=global_timeout,
                    fail_fast=fail_fast,
                )
            )

            # Execute calls in thread pool
            with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
                futures = {
                    executor.submit(
                        self._execute_call,
                        call,
                        cancel_event,
                        global_timeout,
                        start_time,
                    ): call.index
                    for call in calls
                }

                try:
                    for future in as_completed(futures, timeout=global_timeout):
                        index = futures[future]
                        try:
                            result = future.result()
                            results[index] = result

                            # Emit per-call event
                            ctx.event_bus.publish(
                                BatchCallCompleted(
                                    batch_id=batch_id,
                                    call_id=result.call_id,
                                    call_index=result.index,
                                    provider_id=calls[index].provider,
                                    tool_name=calls[index].tool,
                                    success=result.success,
                                    elapsed_ms=result.elapsed_ms,
                                    error_type=result.error_type,
                                )
                            )

                            if result.success:
                                succeeded += 1
                            else:
                                failed += 1
                                if fail_fast:
                                    logger.debug(
                                        "batch_fail_fast_triggered",
                                        batch_id=batch_id,
                                        failed_index=index,
                                    )
                                    cancel_event.set()
                                    BATCH_CANCELLATIONS_TOTAL.inc(reason="fail_fast")
                                    break

                        except Exception as e:
                            # Future raised exception
                            call = calls[index]
                            results[index] = CallResult(
                                index=index,
                                call_id=call.call_id,
                                success=False,
                                error=str(e),
                                error_type=type(e).__name__,
                                elapsed_ms=(time.perf_counter() - start_time) * 1000,
                            )
                            failed += 1

                            if fail_fast:
                                cancel_event.set()
                                BATCH_CANCELLATIONS_TOTAL.inc(reason="fail_fast")
                                break

                except TimeoutError:
                    # Global timeout exceeded
                    logger.warning(
                        "batch_global_timeout",
                        batch_id=batch_id,
                        timeout=global_timeout,
                    )
                    cancel_event.set()
                    BATCH_CANCELLATIONS_TOTAL.inc(reason="timeout")

            # Fill in cancelled/timed out calls
            for i, result in enumerate(results):
                if result is None:
                    call = calls[i]
                    results[i] = CallResult(
                        index=i,
                        call_id=call.call_id,
                        success=False,
                        error="Cancelled" if cancel_event.is_set() else "Timeout",
                        error_type="CancellationError" if cancel_event.is_set() else "TimeoutError",
                        elapsed_ms=(time.perf_counter() - start_time) * 1000,
                    )
                    cancelled += 1

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            success = failed == 0 and cancelled == 0

            # Determine result status for metrics
            if success:
                result_status = "success"
            elif succeeded > 0:
                result_status = "partial"
            else:
                result_status = "failure"

            # Record metrics
            BATCH_CALLS_TOTAL.inc(result=result_status)
            BATCH_SIZE_HISTOGRAM.observe(len(calls))
            BATCH_DURATION_SECONDS.observe(elapsed_ms / 1000)

            # Emit completion event
            ctx.event_bus.publish(
                BatchInvocationCompleted(
                    batch_id=batch_id,
                    total=len(calls),
                    succeeded=succeeded,
                    failed=failed,
                    elapsed_ms=elapsed_ms,
                    cancelled=cancelled,
                )
            )

            logger.info(
                "batch_completed",
                batch_id=batch_id,
                total=len(calls),
                succeeded=succeeded,
                failed=failed,
                cancelled=cancelled,
                elapsed_ms=round(elapsed_ms, 2),
            )

            return BatchResult(
                batch_id=batch_id,
                success=success,
                total=len(calls),
                succeeded=succeeded,
                failed=failed,
                elapsed_ms=elapsed_ms,
                results=[r for r in results if r is not None],
                cancelled=cancelled,
            )

        finally:
            with self._active_lock:
                self._active_batches -= 1
                BATCH_CONCURRENCY_GAUGE.set(self._active_batches)

    def _execute_call(
        self,
        call: CallSpec,
        cancel_event: threading.Event,
        global_timeout: float,
        batch_start_time: float,
    ) -> CallResult:
        """Execute a single call within the batch.

        Handles:
        - Cooperative cancellation
        - Single-flight cold starts
        - Circuit breaker checks
        - Response truncation

        Args:
            call: Call specification.
            cancel_event: Event to check for cancellation.
            global_timeout: Global batch timeout.
            batch_start_time: When batch started (for remaining time calculation).

        Returns:
            CallResult for this call.
        """
        ctx = get_context()
        call_start = time.perf_counter()

        # Check cancellation before starting
        if cancel_event.is_set():
            return CallResult(
                index=call.index,
                call_id=call.call_id,
                success=False,
                error="Cancelled before execution",
                error_type="CancellationError",
                elapsed_ms=0.0,
            )

        try:
            # Calculate effective timeout
            elapsed = time.perf_counter() - batch_start_time
            remaining_global = global_timeout - elapsed
            if remaining_global <= 0:
                return CallResult(
                    index=call.index,
                    call_id=call.call_id,
                    success=False,
                    error="Global timeout exceeded",
                    error_type="TimeoutError",
                    elapsed_ms=0.0,
                )

            effective_timeout = remaining_global
            if call.timeout is not None:
                effective_timeout = min(call.timeout, remaining_global)

            # Get provider (or group)
            provider_obj = PROVIDERS.get(call.provider)
            is_group = False
            if not provider_obj:
                group_obj = GROUPS.get(call.provider)
                if group_obj:
                    is_group = True
                else:
                    return CallResult(
                        index=call.index,
                        call_id=call.call_id,
                        success=False,
                        error=f"Provider '{call.provider}' not found",
                        error_type="ProviderNotFoundError",
                        elapsed_ms=(time.perf_counter() - call_start) * 1000,
                    )

            # Check circuit breaker (for non-group providers)
            if not is_group and provider_obj:
                if hasattr(provider_obj, "health") and provider_obj.health.circuit_breaker_open:
                    BATCH_CIRCUIT_BREAKER_REJECTIONS_TOTAL.inc(provider=call.provider)
                    return CallResult(
                        index=call.index,
                        call_id=call.call_id,
                        success=False,
                        error="Circuit breaker open",
                        error_type="CircuitBreakerOpen",
                        elapsed_ms=(time.perf_counter() - call_start) * 1000,
                    )

            # Single-flight cold start (only for non-group providers)
            if not is_group and provider_obj and provider_obj.state.value == "cold":
                try:
                    self._single_flight.do(
                        call.provider,
                        lambda: ctx.command_bus.send(StartProviderCommand(provider_id=call.provider)),
                    )
                except Exception as e:
                    return CallResult(
                        index=call.index,
                        call_id=call.call_id,
                        success=False,
                        error=f"Failed to start provider: {e}",
                        error_type="ProviderStartError",
                        elapsed_ms=(time.perf_counter() - call_start) * 1000,
                    )

            # Check cancellation after cold start
            if cancel_event.is_set():
                return CallResult(
                    index=call.index,
                    call_id=call.call_id,
                    success=False,
                    error="Cancelled after cold start",
                    error_type="CancellationError",
                    elapsed_ms=(time.perf_counter() - call_start) * 1000,
                )

            # Execute tool invocation
            command = InvokeToolCommand(
                provider_id=call.provider,
                tool_name=call.tool,
                arguments=call.arguments,
                timeout=effective_timeout,
            )
            result = ctx.command_bus.send(command)

            elapsed_ms = (time.perf_counter() - call_start) * 1000

            # Check response size and truncate if needed
            truncated = False
            truncated_reason = None
            original_size = None

            result_json = json.dumps(result)
            result_size = len(result_json.encode("utf-8"))

            if result_size > MAX_RESPONSE_SIZE_BYTES:
                truncated = True
                truncated_reason = "response_size_exceeded"
                original_size = result_size
                result = None
                BATCH_TRUNCATIONS_TOTAL.inc(reason="per_call")
                logger.warning(
                    "batch_call_truncated",
                    call_id=call.call_id,
                    provider=call.provider,
                    tool=call.tool,
                    size_bytes=result_size,
                    limit_bytes=MAX_RESPONSE_SIZE_BYTES,
                )

            logger.debug(
                "batch_call_completed",
                call_id=call.call_id,
                provider=call.provider,
                tool=call.tool,
                success=True,
                elapsed_ms=round(elapsed_ms, 2),
            )

            return CallResult(
                index=call.index,
                call_id=call.call_id,
                success=True,
                result=result,
                elapsed_ms=elapsed_ms,
                truncated=truncated,
                truncated_reason=truncated_reason,
                original_size_bytes=original_size,
            )

        except Exception as e:
            elapsed_ms = (time.perf_counter() - call_start) * 1000
            error_type = type(e).__name__

            logger.debug(
                "batch_call_failed",
                call_id=call.call_id,
                provider=call.provider,
                tool=call.tool,
                error=str(e),
                error_type=error_type,
                elapsed_ms=round(elapsed_ms, 2),
            )

            return CallResult(
                index=call.index,
                call_id=call.call_id,
                success=False,
                error=str(e),
                error_type=error_type,
                elapsed_ms=elapsed_ms,
            )


# Global executor instance
_executor = BatchExecutor()


# =============================================================================
# MCP Tool
# =============================================================================


def hangar_batch(
    calls: list[dict[str, Any]],
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    timeout: float = DEFAULT_TIMEOUT,
    fail_fast: bool = False,
) -> dict[str, Any]:
    """Execute multiple tool invocations in parallel.

    Executes a batch of tool invocations with configurable concurrency and
    timeout. Calls are independent (no dependency ordering) and executed
    in parallel using a thread pool.

    Features:
    - Parallel execution with configurable concurrency
    - Single-flight cold starts (one provider starts once, not N times)
    - Partial success handling (default: continue on error)
    - Fail-fast mode (abort on first error)
    - Response truncation for oversized payloads
    - Circuit breaker integration

    Args:
        calls: List of invocations to execute. Each call must have:
            - provider: str - Provider ID (required)
            - tool: str - Tool name (required)
            - arguments: dict - Tool arguments (required)
            - timeout: float - Per-call timeout in seconds (optional)
        max_concurrency: Maximum parallel workers (1-20, default 10)
        timeout: Global timeout for entire batch (1-300s, default 60)
        fail_fast: If True, abort remaining calls on first error

    Returns:
        BatchResult dict with:
        - batch_id: UUID for tracing
        - success: True if all calls succeeded
        - total: Total number of calls
        - succeeded: Number of successful calls
        - failed: Number of failed calls
        - elapsed_ms: Total batch execution time
        - results: List of per-call results

    Examples:
        # Simple batch
        hangar_batch(calls=[
            {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
            {"provider": "math", "tool": "multiply", "arguments": {"a": 3, "b": 4}},
        ])

        # With fail-fast
        hangar_batch(
            calls=[...],
            fail_fast=True,
        )

        # With per-call timeout
        hangar_batch(calls=[
            {"provider": "fetch", "tool": "get", "arguments": {"url": "..."}, "timeout": 5.0},
        ], timeout=60.0)
    """
    batch_id = str(uuid.uuid4())

    logger.info(
        "batch_requested",
        batch_id=batch_id,
        call_count=len(calls),
        max_concurrency=max_concurrency,
        timeout=timeout,
        fail_fast=fail_fast,
    )

    # Handle empty batch
    if not calls:
        logger.debug("batch_empty", batch_id=batch_id)
        return {
            "batch_id": batch_id,
            "success": True,
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "elapsed_ms": 0.0,
            "results": [],
        }

    # Clamp values to limits
    max_concurrency = max(1, min(max_concurrency, MAX_CONCURRENCY_LIMIT))
    timeout = max(1.0, min(timeout, MAX_TIMEOUT))

    # Eager validation
    validation_errors = _validate_batch(calls, max_concurrency, timeout)
    if validation_errors:
        BATCH_VALIDATION_FAILURES_TOTAL.inc()
        BATCH_CALLS_TOTAL.inc(result="validation_error")
        logger.warning(
            "batch_validation_failed",
            batch_id=batch_id,
            error_count=len(validation_errors),
        )
        return {
            "batch_id": batch_id,
            "success": False,
            "error": "Validation failed",
            "validation_errors": [
                {"index": e.index, "field": e.field, "message": e.message} for e in validation_errors
            ],
        }

    # Build call specs
    call_specs = [
        CallSpec(
            index=i,
            call_id=str(uuid.uuid4()),
            provider=call["provider"],
            tool=call["tool"],
            arguments=call["arguments"],
            timeout=call.get("timeout"),
        )
        for i, call in enumerate(calls)
    ]

    # Execute batch
    result = _executor.execute(
        batch_id=batch_id,
        calls=call_specs,
        max_concurrency=max_concurrency,
        global_timeout=timeout,
        fail_fast=fail_fast,
    )

    # Convert to dict response
    return {
        "batch_id": result.batch_id,
        "success": result.success,
        "total": result.total,
        "succeeded": result.succeeded,
        "failed": result.failed,
        "elapsed_ms": round(result.elapsed_ms, 2),
        "results": [
            {
                "index": r.index,
                "call_id": r.call_id,
                "success": r.success,
                "result": r.result,
                "error": r.error,
                "error_type": r.error_type,
                "elapsed_ms": round(r.elapsed_ms, 2),
                **({"truncated": r.truncated} if r.truncated else {}),
                **({"truncated_reason": r.truncated_reason} if r.truncated_reason else {}),
                **({"original_size_bytes": r.original_size_bytes} if r.original_size_bytes else {}),
            }
            for r in result.results
        ],
    }


# =============================================================================
# Tool Registration
# =============================================================================


def register_batch_tools(mcp: FastMCP) -> None:
    """Register batch invocation tools with the MCP server.

    Args:
        mcp: FastMCP server instance.
    """
    mcp.tool()(hangar_batch)
    logger.info("batch_tools_registered")
