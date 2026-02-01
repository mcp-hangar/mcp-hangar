"""Batch execution engine.

Provides parallel execution of batch invocations with:
- ThreadPoolExecutor for concurrent execution
- Single-flight pattern for cold starts
- Cooperative cancellation
- Circuit breaker integration
- Response truncation
"""

from concurrent.futures import as_completed, ThreadPoolExecutor
import json
import threading
import time
from typing import Any

from ....application.commands import InvokeToolCommand, StartProviderCommand
from ....domain.events import BatchCallCompleted, BatchInvocationCompleted, BatchInvocationRequested
from ....infrastructure.single_flight import SingleFlight
from ....logging_config import get_logger
from ....metrics import (
    BATCH_CALLS_TOTAL,
    BATCH_CANCELLATIONS_TOTAL,
    BATCH_CIRCUIT_BREAKER_REJECTIONS_TOTAL,
    BATCH_CONCURRENCY_GAUGE,
    BATCH_DURATION_SECONDS,
    BATCH_SIZE_HISTOGRAM,
    BATCH_TRUNCATIONS_TOTAL,
)
from ....retry import retry_sync, RetryPolicy, RetryResult
from ...context import get_context
from ...state import GROUPS
from .models import BatchResult, CallResult, CallSpec, MAX_RESPONSE_SIZE_BYTES, RetryMetadata

logger = get_logger(__name__)


class BatchExecutor:
    """Executes batch invocations with parallel processing."""

    def __init__(self):
        self._single_flight = SingleFlight(cache_results=False)
        self._active_batches = 0
        self._active_lock = threading.Lock()

    def _apply_batch_truncation(self, batch_id: str, results: list[CallResult]) -> list[CallResult]:
        """Apply batch-level truncation if enabled and needed.

        Args:
            batch_id: The batch identifier.
            results: List of call results to potentially truncate.

        Returns:
            List of results, potentially with some truncated.
        """
        from ...bootstrap.truncation import get_truncation_manager

        truncation_manager = get_truncation_manager()
        if truncation_manager is None:
            return results

        return truncation_manager.process_batch(batch_id, results)

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

            # Apply batch-level truncation if enabled
            final_results = [r for r in results if r is not None]
            final_results = self._apply_batch_truncation(batch_id, final_results)

            return BatchResult(
                batch_id=batch_id,
                success=success,
                total=len(calls),
                succeeded=succeeded,
                failed=failed,
                elapsed_ms=elapsed_ms,
                results=final_results,
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
        - Retry with exponential backoff

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
        provider_obj = ctx.get_provider(call.provider)
        is_group = False
        if not provider_obj:
            group_obj = GROUPS.get(call.provider)
            if group_obj:
                is_group = True
            elif not ctx.provider_exists(call.provider):
                return CallResult(
                    index=call.index,
                    call_id=call.call_id,
                    success=False,
                    error=f"Provider '{call.provider}' not found",
                    error_type="ProviderNotFoundError",
                    elapsed_ms=(time.perf_counter() - call_start) * 1000,
                )

        # Check circuit breaker / health degradation (for non-group providers)
        if not is_group and provider_obj:
            if hasattr(provider_obj, "health") and provider_obj.health.should_degrade():
                BATCH_CIRCUIT_BREAKER_REJECTIONS_TOTAL.inc(provider=call.provider)
                return CallResult(
                    index=call.index,
                    call_id=call.call_id,
                    success=False,
                    error="Circuit breaker open (too many consecutive failures)",
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

        # Define the invocation operation for retry
        def do_invoke() -> dict[str, Any]:
            command = InvokeToolCommand(
                provider_id=call.provider,
                tool_name=call.tool,
                arguments=call.arguments,
                timeout=effective_timeout,
            )
            return ctx.command_bus.send(command)

        # Execute with retry if max_retries > 1
        retry_result: RetryResult | None = None
        if call.max_retries > 1:
            policy = RetryPolicy(max_attempts=call.max_retries)
            retry_result = retry_sync(
                operation=do_invoke,
                policy=policy,
                provider=call.provider,
                operation_name=call.tool,
            )
            if retry_result.success:
                result = retry_result.result
            else:
                # All retries exhausted
                elapsed_ms = (time.perf_counter() - call_start) * 1000
                error_type = type(retry_result.final_error).__name__ if retry_result.final_error else "UnknownError"
                error_msg = str(retry_result.final_error) if retry_result.final_error else "Unknown error"

                logger.debug(
                    "batch_call_failed",
                    call_id=call.call_id,
                    provider=call.provider,
                    tool=call.tool,
                    error=error_msg,
                    error_type=error_type,
                    elapsed_ms=round(elapsed_ms, 2),
                    retry_attempts=retry_result.attempt_count,
                )

                return CallResult(
                    index=call.index,
                    call_id=call.call_id,
                    success=False,
                    error=error_msg,
                    error_type=error_type,
                    elapsed_ms=elapsed_ms,
                    retry_metadata=RetryMetadata(
                        attempts=retry_result.attempt_count,
                        retries=[a.error_type for a in retry_result.attempts],
                        total_time_ms=retry_result.total_time_s * 1000,
                    ),
                )
        else:
            # No retry - direct execution
            try:
                result = do_invoke()
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
            retry_attempts=retry_result.attempt_count if retry_result else 1,
        )

        # Build retry metadata if retries were used
        retry_meta = None
        if retry_result:
            retry_meta = RetryMetadata(
                attempts=retry_result.attempt_count,
                retries=[a.error_type for a in retry_result.attempts],
                total_time_ms=retry_result.total_time_s * 1000,
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
            retry_metadata=retry_meta,
        )


def format_result_dict(r: CallResult) -> dict[str, Any]:
    """Format a CallResult as a dictionary for API response.

    Args:
        r: The CallResult to format.

    Returns:
        Dictionary with result fields.
    """
    result_dict: dict[str, Any] = {
        "index": r.index,
        "call_id": r.call_id,
        "success": r.success,
        "result": r.result,
        "error": r.error,
        "error_type": r.error_type,
        "elapsed_ms": round(r.elapsed_ms, 2),
    }

    if r.truncated:
        result_dict["truncated"] = r.truncated
    if r.truncated_reason:
        result_dict["truncated_reason"] = r.truncated_reason
    if r.original_size_bytes is not None:
        result_dict["original_size_bytes"] = r.original_size_bytes
    if r.continuation_id:
        result_dict["continuation_id"] = r.continuation_id
    if r.retry_metadata:
        result_dict["retry_metadata"] = r.retry_metadata.to_dict()

    return result_dict
