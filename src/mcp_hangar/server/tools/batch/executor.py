"""Batch execution engine.

Provides parallel execution of batch invocations with:
- ThreadPoolExecutor for concurrent execution
- Two-level semaphore concurrency control (global + per-provider)
- Single-flight pattern for cold starts
- Cooperative cancellation
- Circuit breaker integration
- Response truncation
"""

from concurrent.futures import as_completed, ThreadPoolExecutor
import asyncio
import json
import threading
import time
from typing import Any

from ....application.commands import InvokeToolCommand, StartProviderCommand
from ....domain.events import BatchCallCompleted, BatchInvocationCompleted, BatchInvocationRequested
from ....domain.services import get_tool_access_resolver
from ....infrastructure.single_flight import SingleFlight
from ....logging_config import get_logger
from ....observability.tracing import extract_trace_context, get_tracer
from ....metrics import (
    BATCH_CALLS_TOTAL,
    BATCH_CANCELLATIONS_TOTAL,
    BATCH_CIRCUIT_BREAKER_REJECTIONS_TOTAL,
    BATCH_CONCURRENCY_GAUGE,
    BATCH_DURATION_SECONDS,
    BATCH_SIZE_HISTOGRAM,
    BATCH_TRUNCATIONS_TOTAL,
    TOOL_ACCESS_DENIED_TOTAL,
)
from ....retry import retry_sync, RetryPolicy, RetryResult
from ...context import get_context
from ...state import GROUPS
from .concurrency import ConcurrencyManager, get_concurrency_manager
from .models import BatchResult, CallResult, CallSpec, MAX_RESPONSE_SIZE_BYTES, RetryMetadata

logger = get_logger(__name__)


class BatchExecutor:
    """Executes batch invocations with parallel processing.

    Uses a two-level concurrency model:
    1. ThreadPoolExecutor(max_workers=N) provides per-batch thread management.
       N is the effective batch concurrency: min(user_param, global_limit).
    2. ConcurrencyManager provides cross-batch, system-wide concurrency control
       via global and per-provider semaphores.

    All calls in a batch are submitted to the thread pool at once. Each worker
    thread acquires global + provider semaphores before executing, providing
    backpressure without sequential chunking. Fast calls release their slots
    immediately, allowing queued calls to proceed without waiting for the
    entire batch wave to complete.
    """

    def __init__(self, concurrency_manager: ConcurrencyManager | None = None):
        self._single_flight = SingleFlight(cache_results=False)
        self._active_batches = 0
        self._active_lock = threading.Lock()
        self._concurrency_manager = concurrency_manager

    @property
    def concurrency_manager(self) -> ConcurrencyManager:
        """Get the concurrency manager (lazy-loaded from singleton if not injected)."""
        if self._concurrency_manager is None:
            self._concurrency_manager = get_concurrency_manager()
        return self._concurrency_manager

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

    def _check_approval_gate(
        self,
        call: CallSpec,
        resolver: Any,
        ctx: Any,
    ) -> CallResult | None:
        """Check if the tool requires approval and block until resolved.

        Returns None if no approval is needed (continue execution).
        Returns a CallResult if the tool was denied or timed out.
        """
        # Get effective policy for this provider (or fallback to _global)
        policy = resolver.resolve_effective_policy(call.provider)
        if policy.is_unrestricted():
            # Check global policy fallback
            policy = resolver.resolve_effective_policy("_global")
            if policy.is_unrestricted():
                return None

        if not policy.requires_approval(call.tool):
            return None

        # Tool requires approval -- delegate to ApprovalGateService
        gate_service = getattr(ctx, "approval_gate", None)
        if gate_service is None:
            logger.debug("approval_gate_not_configured", tool=call.tool)
            return None

        logger.info(
            "approval_gate_blocking",
            provider=call.provider,
            tool=call.tool,
            call_id=call.call_id,
        )

        try:
            # ApprovalGateService.check() is async.  We cannot use
            # run_coroutine_threadsafe on the main event loop because
            # FastMCP calls sync tools directly on the event loop thread,
            # which is blocked inside hangar_call() -> execute() right now.
            #
            # Instead we create a fresh per-thread event loop.  aiosqlite
            # opens a new connection per call (no shared state), so this is
            # safe.  The hold_registry uses asyncio.Event which is bound to
            # whichever loop it is first awaited on -- here that's the
            # per-thread loop.  The resolve() endpoint will need to signal
            # the same hold_registry entry; we handle this by using
            # threading.Event as the cross-loop signaling mechanism instead
            # of asyncio.Event.
            thread_loop = asyncio.new_event_loop()
            try:
                result = thread_loop.run_until_complete(
                    gate_service.check(
                        provider_id=call.provider,
                        tool_name=call.tool,
                        arguments=call.arguments,
                        policy=policy,
                        correlation_id=call.call_id,
                    )
                )
            finally:
                thread_loop.close()
        except (RuntimeError, OSError, ValueError, TimeoutError) as exc:
            logger.warning("approval_gate_error", tool=call.tool, error=str(exc))
            return CallResult(
                index=call.index,
                call_id=call.call_id,
                success=False,
                error=f"Approval gate error: {exc}",
                error_type="ApprovalGateError",
                elapsed_ms=0,
            )

        if result.approved and result.approval_id is None:
            # not_required -- no approval was needed after detailed check
            return None

        if not result.approved:
            return CallResult(
                index=call.index,
                call_id=call.call_id,
                success=False,
                error=result.reason or "Tool execution denied by approval gate",
                error_type=result.error_code or "ApprovalDenied",
                elapsed_ms=0,
            )

        # Approved -- continue execution
        return None

    def execute(
        self,
        batch_id: str,
        calls: list[CallSpec],
        max_concurrency: int,
        global_timeout: float,
        fail_fast: bool,
    ) -> BatchResult:
        """Execute batch of calls in parallel.

        All calls are submitted to the thread pool immediately. Concurrency is
        controlled by two mechanisms:
        - ThreadPoolExecutor max_workers: caps threads for this batch
        - ConcurrencyManager semaphores: caps in-flight calls globally and per-provider

        The effective per-batch thread count is min(max_concurrency, global_limit)
        when the global limit is set, ensuring we don't create more threads than
        the system-wide limit allows.

        Args:
            batch_id: Unique batch identifier.
            calls: List of call specifications.
            max_concurrency: Maximum parallel workers for this batch.
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

        # Determine effective thread pool size:
        # - Capped by the per-batch max_concurrency (user/default)
        # - Also capped by global concurrency limit (no point creating more
        #   threads than the global semaphore will allow through)
        cm = self.concurrency_manager
        global_limit = cm.global_limit
        if global_limit > 0:
            effective_workers = min(max_concurrency, global_limit)
        else:
            effective_workers = max_concurrency

        tracer = get_tracer(__name__)

        # Track active batches for metrics
        with self._active_lock:
            self._active_batches += 1
            BATCH_CONCURRENCY_GAUGE.set(self._active_batches)

        try:
          with tracer.start_as_current_span("batch.execute") as batch_span:
            batch_span.set_attribute("batch.id", batch_id)
            batch_span.set_attribute("batch.call_count", len(calls))
            batch_span.set_attribute("batch.max_concurrency", max_concurrency)
            batch_span.set_attribute("batch.timeout", global_timeout)
            batch_span.set_attribute("batch.fail_fast", fail_fast)
            batch_span.set_attribute("batch.effective_workers", effective_workers)

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

            logger.debug(
                "batch_dispatch_start",
                batch_id=batch_id,
                call_count=len(calls),
                effective_workers=effective_workers,
                global_limit=global_limit if global_limit > 0 else "unlimited",
                provider_count=len(providers),
            )

            # Execute calls in thread pool — all submitted at once, semaphores
            # provide backpressure (not sequential chunking)
            with ThreadPoolExecutor(max_workers=effective_workers) as executor:
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

                        except Exception as e:  # noqa: BLE001 -- fault-barrier: future exception handling for batch result collection
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

            # After the ThreadPoolExecutor context manager exits (shutdown(wait=True)),
            # some futures may have completed after as_completed timed out (e.g.
            # approval-gated calls that were waiting for human decision).  Collect
            # those results before marking anything as cancelled.
            for future, index in futures.items():
                if results[index] is not None:
                    continue  # already collected
                if future.done():
                    try:
                        result = future.result(timeout=0)
                        results[index] = result
                        if result.success:
                            succeeded += 1
                        else:
                            failed += 1
                    except Exception as e:  # noqa: BLE001
                        results[index] = CallResult(
                            index=index,
                            call_id=calls[index].call_id,
                            success=False,
                            error=str(e),
                            error_type=type(e).__name__,
                            elapsed_ms=(time.perf_counter() - start_time) * 1000,
                        )
                        failed += 1

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

            # Record batch outcome on span
            batch_span.set_attribute("batch.succeeded", succeeded)
            batch_span.set_attribute("batch.failed", failed)
            batch_span.set_attribute("batch.cancelled", cancelled)
            batch_span.set_attribute("batch.result", result_status)
            batch_span.set_attribute("batch.elapsed_ms", round(elapsed_ms, 2))

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

        Acquires global and per-provider concurrency slots via the
        ConcurrencyManager before performing the actual invocation.
        This ensures system-wide and per-provider backpressure even
        when multiple batches run concurrently.

        Handles:
        - Cooperative cancellation
        - Two-level concurrency control (global + per-provider)
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

        # Extract W3C TraceContext from call metadata for distributed tracing.
        # If the agent passed traceparent, spans created for this call become
        # children of the agent's trace rather than new root spans.
        metadata = call.metadata or {}
        parent_context = extract_trace_context(metadata)

        # Create a span for this batch call, parented to the agent's trace
        # context when traceparent was provided. This links the Hangar span
        # to the upstream agent trace for end-to-end distributed tracing.
        tracer = get_tracer(__name__)
        span_ctx_kwargs = {}
        if parent_context is not None:
            span_ctx_kwargs["context"] = parent_context
        with tracer.start_as_current_span(
            f"batch.call.{call.tool}",
            **span_ctx_kwargs,
        ) as span:
            span.set_attribute("mcp.provider.id", call.provider)
            span.set_attribute("mcp.tool.name", call.tool)
            span.set_attribute("batch.call.id", call.call_id)
            return self._execute_call_inner(
                call,
                cancel_event,
                global_timeout,
                batch_start_time,
                ctx,
                call_start,
            )

    def _execute_call_inner(
        self,
        call: CallSpec,
        cancel_event: threading.Event,
        global_timeout: float,
        batch_start_time: float,
        ctx: Any,
        call_start: float,
    ) -> CallResult:
        """Inner execution logic for a single batch call (runs inside trace span).

        Separated from _execute_call so the span wraps the full call lifecycle.
        """
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

        # Check tool access policy BEFORE starting provider or executing
        # This is config-driven filtering, identity-agnostic (runs before RBAC)
        resolver = get_tool_access_resolver()
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("policy.check_access") as policy_span:
            policy_span.set_attribute("mcp.provider.id", call.provider)
            policy_span.set_attribute("mcp.tool.name", call.tool)
            policy_span.set_attribute("policy.is_group", is_group)
            if is_group:
                group_obj = GROUPS.get(call.provider)
                # For groups, we check against group policy
                # Member-specific policy will be checked when member is selected
                allowed = resolver.is_tool_allowed(
                    provider_id=call.provider,
                    tool_name=call.tool,
                    group_id=call.provider,
                )
            else:
                # For standalone providers
                allowed = resolver.is_tool_allowed(
                    provider_id=call.provider,
                    tool_name=call.tool,
                )
            policy_span.set_attribute("policy.allowed", allowed)

        if not allowed:
            logger.info(
                "tool_access_denied",
                provider_id=call.provider,
                tool=call.tool,
                reason="tool_not_in_access_policy",
            )
            TOOL_ACCESS_DENIED_TOTAL.inc(
                provider=call.provider,
                tool=call.tool,
                reason="tool_not_in_access_policy",
            )
            return CallResult(
                index=call.index,
                call_id=call.call_id,
                success=False,
                error="Tool not available for this provider",
                error_type="ToolAccessDeniedError",
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

        # Approval gate: check if the tool requires human approval before execution.
        # The policy is set by the agent via POST /api/agent/policy.
        # Uses the resolver's effective policy (provider-specific or _global fallback).
        with tracer.start_as_current_span("approval_gate.check") as approval_span:
            approval_span.set_attribute("mcp.provider.id", call.provider)
            approval_span.set_attribute("mcp.tool.name", call.tool)
            approval_result = self._check_approval_gate(call, resolver, ctx)
            if approval_result is not None:
                approval_span.set_attribute("approval.result", approval_result.error_type or "denied")
                approval_result.elapsed_ms = (time.perf_counter() - call_start) * 1000
                return approval_result
            approval_span.set_attribute("approval.result", "not_required")

        # Single-flight cold start (only for non-group providers)
        if not is_group and provider_obj and provider_obj.state.value == "cold":
            with tracer.start_as_current_span("provider.cold_start") as cs_span:
                cs_span.set_attribute("mcp.provider.id", call.provider)
                try:
                    self._single_flight.do(
                        call.provider,
                        lambda: ctx.command_bus.send(StartProviderCommand(provider_id=call.provider)),
                    )
                    cs_span.set_attribute("cold_start.result", "success")
                except Exception as e:  # noqa: BLE001 -- fault-barrier: provider start failure must return error result, not crash batch
                    cs_span.set_attribute("cold_start.result", "error")
                    cs_span.record_exception(e)
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

        # Acquire concurrency slots (global + per-provider) before invocation.
        # This is where backpressure happens: if the global or provider semaphore
        # is full, this thread blocks until a slot frees up. Crucially, the call
        # starts as soon as ANY slot is freed -- it does not wait for an entire
        # batch wave to complete (unlike sequential chunking).
        cm = self.concurrency_manager
        with tracer.start_as_current_span("concurrency.acquire") as conc_span:
            conc_span.set_attribute("mcp.provider.id", call.provider)
            with cm.acquire(call.provider) as wait_s:
                conc_span.set_attribute("concurrency.wait_ms", round(wait_s * 1000, 2))
                if wait_s > 0.01:
                    logger.debug(
                        "concurrency_slot_wait",
                        call_id=call.call_id,
                        provider=call.provider,
                        wait_ms=round(wait_s * 1000, 2),
                    )

                return self._invoke_with_retry(call, cancel_event, effective_timeout, call_start, ctx)

    def _invoke_with_retry(
        self,
        call: CallSpec,
        cancel_event: threading.Event,
        effective_timeout: float,
        call_start: float,
        ctx: Any,
    ) -> CallResult:
        """Perform the tool invocation, optionally with retries.

        This method runs while concurrency slots are held. It contains the
        actual I/O (command bus send) and retry logic extracted from
        _execute_call for clarity.

        Args:
            call: Call specification.
            cancel_event: Event to check for cancellation.
            effective_timeout: Timeout for this call.
            call_start: Monotonic time when the call started.
            ctx: Application context.

        Returns:
            CallResult for this call.
        """

        # Define the invocation operation for retry
        tracer = get_tracer(__name__)

        def do_invoke() -> dict[str, Any]:
            with tracer.start_as_current_span("command.send.InvokeToolCommand") as cmd_span:
                cmd_span.set_attribute("mcp.provider.id", call.provider)
                cmd_span.set_attribute("mcp.tool.name", call.tool)
                cmd_span.set_attribute("command.timeout", effective_timeout)
                command = InvokeToolCommand(
                    provider_id=call.provider,
                    tool_name=call.tool,
                    arguments=call.arguments,
                    timeout=effective_timeout,
                )
                result = ctx.command_bus.send(command)
                cmd_span.set_attribute("command.result", "success")
                return result

        # Execute with retry if max_retries > 1
        retry_result: RetryResult | None = None
        if call.max_retries > 1:
            with tracer.start_as_current_span("invoke_with_retry") as retry_span:
                retry_span.set_attribute("retry.max_attempts", call.max_retries)
                retry_span.set_attribute("mcp.provider.id", call.provider)
                retry_span.set_attribute("mcp.tool.name", call.tool)
                policy = RetryPolicy(max_attempts=call.max_retries)
                retry_result = retry_sync(
                    operation=do_invoke,
                    policy=policy,
                    provider=call.provider,
                    operation_name=call.tool,
                )
                retry_span.set_attribute("retry.attempts", retry_result.attempt_count)
                retry_span.set_attribute("retry.success", retry_result.success)
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
            except Exception as e:  # noqa: BLE001 -- fault-barrier: tool invocation failure must return error result, not crash batch
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


def format_result_dict(result: CallResult) -> dict[str, Any]:
    """Format a CallResult into a response dictionary.

    Args:
        result: The call result to format.

    Returns:
        Dictionary suitable for JSON serialization.
    """
    d: dict[str, Any] = {
        "index": result.index,
        "call_id": result.call_id,
        "success": result.success,
        "result": result.result,
        "error": result.error,
        "error_type": result.error_type,
        "elapsed_ms": round(result.elapsed_ms, 2),
    }

    if result.truncated:
        d["truncated"] = True
        d["truncated_reason"] = result.truncated_reason
        d["original_size_bytes"] = result.original_size_bytes

    if result.continuation_id:
        d["continuation_id"] = result.continuation_id

    if result.retry_metadata:
        d["retry_metadata"] = result.retry_metadata.to_dict()

    return d
