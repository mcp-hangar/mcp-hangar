"""Batch execution engine.

Provides parallel execution of batch invocations with:
- ThreadPoolExecutor for concurrent execution
- Two-level semaphore concurrency control (global + per-mcp_server)
- Single-flight pattern for cold starts
- Cooperative cancellation
- Circuit breaker integration
- Response truncation
"""

from concurrent.futures import as_completed, ThreadPoolExecutor
import asyncio
import atexit
import contextvars
import json
import threading
import time
from typing import Any, cast


from ....application.commands import InvokeToolCommand, StartMcpServerCommand
from ....domain.events import (
    BatchCallCompleted,
    BatchInvocationCompleted,
    BatchInvocationRequested,
    ToolWithdrawnRejected,
)
from ....context import get_identity_context
from ....application.read_models.tool_projection import get_tool_projection_registry
from ....domain.services import get_tool_access_resolver
from ....domain.services.digest_validator import DigestValidator
from ....domain.value_objects import DigestEnforcement, DigestPolicy, DigestUnknownPolicy
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

_approval_loop_local = threading.local()
_all_approval_loops: set[asyncio.AbstractEventLoop] = set()


def _get_approval_loop() -> asyncio.AbstractEventLoop:
    """Return a thread-local event loop for synchronous approval gate calls.

    A fresh loop is created on first access per thread and reused for the
    thread's lifetime. ThreadPoolExecutor reuses worker threads, so amortizes
    loop setup cost across all approval-gated calls in that thread.

    Cross-loop signaling rationale (preserved from original design):
    The hold_registry uses threading.Event (not asyncio.Event) for resolve()
    notifications, because resolve() runs on FastMCP's main loop while
    check() awaits here on a different per-thread loop. Loop reuse does not
    change this -- threading.Event remains the correct signaling primitive.
    """
    loop = getattr(_approval_loop_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _approval_loop_local.loop = loop
        _all_approval_loops.add(loop)
    return loop


@atexit.register
def _close_approval_loops() -> None:
    """Close any thread-local approval gate event loops at interpreter shutdown."""
    for loop in list(_all_approval_loops):
        try:
            if not loop.is_closed():
                loop.close()
        except Exception:  # noqa: BLE001 -- best-effort shutdown
            pass


class BatchExecutor:
    """Executes batch invocations with parallel processing.

    Uses a two-level concurrency model:
    1. ThreadPoolExecutor(max_workers=N) provides per-batch thread management.
       N is the effective batch concurrency: min(user_param, global_limit).
    2. ConcurrencyManager provides cross-batch, system-wide concurrency control
       via global and per-mcp_server semaphores.

    All calls in a batch are submitted to the thread pool at once. Each worker
    thread acquires global + mcp_server semaphores before executing, providing
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
        # Get effective policy for this mcp_server (or fallback to _global)
        policy = resolver.resolve_effective_policy(call.mcp_server)
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
            mcp_server=call.mcp_server,
            tool=call.tool,
            call_id=call.call_id,
        )

        try:
            # ApprovalGateService.check() is async; we run it on a thread-local
            # event loop reused across calls in this worker thread. We cannot
            # use the main FastMCP loop because hangar_call() blocks it. See
            # _get_approval_loop() for the cross-loop signaling rationale.
            thread_loop = _get_approval_loop()
            result = thread_loop.run_until_complete(
                gate_service.check(
                    mcp_server_id=call.mcp_server,
                    tool_name=call.tool,
                    arguments=call.arguments,
                    policy=policy,
                    correlation_id=call.call_id,
                )
            )
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
        - ConcurrencyManager semaphores: caps in-flight calls globally and per-mcp_server

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
                mcp_servers = list(set(c.mcp_server for c in calls))
                ctx.event_bus.publish(
                    BatchInvocationRequested(
                        batch_id=batch_id,
                        call_count=len(calls),
                        mcp_servers=mcp_servers,
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
                    mcp_server_count=len(mcp_servers),
                )

                # Execute calls in thread pool — all submitted at once, semaphores
                # provide backpressure (not sequential chunking).
                # copy_context() snapshots the calling thread's contextvars
                # (identity_context_var, OTel trace context, structlog ctx, …)
                # so each worker inherits the per-request context rather than
                # getting the default empty context that ThreadPoolExecutor
                # would otherwise provide.
                # IMPORTANT: each call gets its own copy — a Context object
                # cannot be entered by more than one thread simultaneously.
                with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                    futures = {
                        executor.submit(
                            contextvars.copy_context().run,
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
                                        mcp_server_id=calls[index].mcp_server,
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
                for i, r in enumerate(results):
                    if r is None:
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

        Acquires global and per-mcp_server concurrency slots via the
        ConcurrencyManager before performing the actual invocation.
        This ensures system-wide and per-mcp_server backpressure even
        when multiple batches run concurrently.

        Handles:
        - Cooperative cancellation
        - Two-level concurrency control (global + per-mcp_server)
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
            span.set_attribute("mcp.server.id", call.mcp_server)
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

        # Read caller tenant_id first: a group's member selection may be
        # tenant-aware (per-tenant canary / version routing, #275). The identity
        # is set by IdentityMiddleware and carried into this worker thread via
        # copy_context() (PR #239).
        _identity_ctx = get_identity_context()
        _caller_tenant_id: str | None = _identity_ctx.caller.tenant_id if _identity_ctx is not None else None

        # Get mcp_server (or group). For a group, select a member NOW (tenant-aware
        # when a canary policy is set) so the rest of the pipeline -- cold-start,
        # circuit breaker, dispatch -- targets a real backend. Policy, withdrawal,
        # and digest-pin checks below still key on the logical group id.
        mcp_server_obj = ctx.get_mcp_server(call.mcp_server)
        is_group = False
        group_obj = None
        target_server_id = call.mcp_server
        if not mcp_server_obj:
            group_obj = GROUPS.get(call.mcp_server)
            if group_obj:
                is_group = True
                selected_member = group_obj.select_member_for(_caller_tenant_id)
                if selected_member is None:
                    return CallResult(
                        index=call.index,
                        call_id=call.call_id,
                        success=False,
                        error=f"No available member in group '{call.mcp_server}'",
                        error_type="NoAvailableMemberError",
                        elapsed_ms=(time.perf_counter() - call_start) * 1000,
                    )
                mcp_server_obj = selected_member
                target_server_id = selected_member.id.value
            elif not ctx.mcp_server_exists(call.mcp_server):
                return CallResult(
                    index=call.index,
                    call_id=call.call_id,
                    success=False,
                    error=f"McpServer '{call.mcp_server}' not found",
                    error_type="McpServerNotFoundError",
                    elapsed_ms=(time.perf_counter() - call_start) * 1000,
                )

        # Check tool access policy BEFORE starting mcp_server or executing.
        resolver = get_tool_access_resolver()
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("policy.check_access") as policy_span:
            policy_span.set_attribute("mcp.server.id", call.mcp_server)
            policy_span.set_attribute("mcp.tool.name", call.tool)
            policy_span.set_attribute("policy.is_group", is_group)
            if is_group:
                group_obj = GROUPS.get(call.mcp_server)
                # For groups, we check against group policy
                # Member-specific policy will be checked when member is selected
                allowed = resolver.is_tool_allowed(
                    mcp_server_id=call.mcp_server,
                    tool_name=call.tool,
                    group_id=call.mcp_server,
                    member_id=_caller_tenant_id,
                )
            else:
                # For standalone mcp_servers: server→member merge when tenant is known
                allowed = resolver.is_tool_allowed(
                    mcp_server_id=call.mcp_server,
                    tool_name=call.tool,
                    member_id=_caller_tenant_id,
                )
            policy_span.set_attribute("policy.allowed", allowed)

        if not allowed:
            logger.info(
                "tool_access_denied",
                mcp_server_id=call.mcp_server,
                tool=call.tool,
                reason="tool_not_in_access_policy",
            )
            TOOL_ACCESS_DENIED_TOTAL.inc(
                mcp_server=call.mcp_server,
                tool=call.tool,
                reason="tool_not_in_access_policy",
            )
            return CallResult(
                index=call.index,
                call_id=call.call_id,
                success=False,
                error="Tool not available for this mcp_server",
                error_type="ToolAccessDeniedError",
                elapsed_ms=(time.perf_counter() - call_start) * 1000,
            )

        # Check tool withdrawal status BEFORE backend invoke (#231).
        # Guarantee: per-process-after-reload (registry is config-reload-driven; runtime
        # mutation is #235). Rejection is envelope-level; protocol-clean -32601 is #232.
        # Semantics: proj is None → registry unpopulated → do NOT block (safe default).
        # Only an explicit is_withdrawn_for() == True causes rejection.
        _proj_registry = get_tool_projection_registry()
        _proj = _proj_registry.resolve(call.mcp_server, call.tool, _caller_tenant_id)
        if _proj is not None and _proj.is_withdrawn_for(_caller_tenant_id):
            logger.info(
                "tool_withdrawn_rejected",
                mcp_server_id=call.mcp_server,
                tool=call.tool,
                tenant_id=_caller_tenant_id,
            )
            ctx.event_bus.publish(
                ToolWithdrawnRejected(
                    tenant_id=_caller_tenant_id,
                    mcp_server=call.mcp_server,
                    tool=call.tool,
                )
            )
            return CallResult(
                index=call.index,
                call_id=call.call_id,
                success=False,
                error=f"Tool '{call.tool}' is withdrawn for this tenant",
                error_type="ToolWithdrawnError",
                elapsed_ms=(time.perf_counter() - call_start) * 1000,
            )

        # Per-tenant digest pin enforcement (#233): if the caller's tenant pinned
        # this tool to an approved digest, validate the backend's current schema
        # against it and enforce per the server's configured mode. This is the
        # first call site for DigestValidator. No pin -> unchanged behavior.
        # NOTE: the withdrawal check above takes precedence -- a withdrawn tool is
        # rejected before reaching here, so no mismatch event fires for a tool that
        # is both withdrawn and pinned.
        _pin = _proj_registry.resolve_pin(call.mcp_server, call.tool, _caller_tenant_id)
        if _pin is not None and _proj is not None:
            _enforcement = _proj_registry.digest_enforcement(call.mcp_server)
            try:
                _digest_result = DigestValidator(
                    DigestPolicy(
                        enforcement=_enforcement,
                        unknown=DigestUnknownPolicy.BLOCK,
                        allowlist=frozenset({_pin}),
                    )
                ).validate_tool(_proj.schema, call.mcp_server, call.call_id, tenant_id=_caller_tenant_id)
                _digest_blocked = _digest_result.blocked
                _digest_event = _digest_result.event
            except Exception:  # noqa: BLE001 -- a malformed projection schema must not 500 the call path
                # Cannot compute/verify the digest: fail closed under block, else allow.
                logger.warning(
                    "tool_digest_pin_unverifiable",
                    mcp_server_id=call.mcp_server,
                    tool=call.tool,
                    tenant_id=_caller_tenant_id,
                )
                _digest_blocked = _enforcement == DigestEnforcement.BLOCK
                _digest_event = None
            if _digest_event is not None:
                ctx.event_bus.publish(_digest_event)
            if _digest_blocked:
                logger.info(
                    "tool_digest_pin_rejected",
                    mcp_server_id=call.mcp_server,
                    tool=call.tool,
                    tenant_id=_caller_tenant_id,
                )
                return CallResult(
                    index=call.index,
                    call_id=call.call_id,
                    success=False,
                    error=f"Tool '{call.tool}' schema does not match the digest pinned for this tenant",
                    error_type="ToolDigestMismatchError",
                    elapsed_ms=(time.perf_counter() - call_start) * 1000,
                )

        # Check circuit breaker / health degradation of the resolved target
        # (a standalone server, or the selected group member).
        if mcp_server_obj:
            if hasattr(mcp_server_obj, "health") and mcp_server_obj.health.should_degrade():
                BATCH_CIRCUIT_BREAKER_REJECTIONS_TOTAL.inc(mcp_server=target_server_id)
                return CallResult(
                    index=call.index,
                    call_id=call.call_id,
                    success=False,
                    error="Circuit breaker open (too many consecutive failures)",
                    error_type="CircuitBreakerOpen",
                    elapsed_ms=(time.perf_counter() - call_start) * 1000,
                )

        # Approval gate: check if the tool requires human approval before execution.
        # The policy is configured via the server config and applied to the ToolAccessResolver.
        # Uses the resolver's effective policy (mcp_server-specific or _global fallback).
        with tracer.start_as_current_span("approval_gate.check") as approval_span:
            approval_span.set_attribute("mcp.server.id", call.mcp_server)
            approval_span.set_attribute("mcp.tool.name", call.tool)
            approval_result = self._check_approval_gate(call, resolver, ctx)
            if approval_result is not None:
                approval_span.set_attribute("approval.result", approval_result.error_type or "denied")
                approval_result.elapsed_ms = (time.perf_counter() - call_start) * 1000
                return approval_result
            approval_span.set_attribute("approval.result", "not_required")

        # Single-flight cold start of the resolved target (standalone server or
        # the selected group member).
        if mcp_server_obj and mcp_server_obj.state.value == "cold":
            with tracer.start_as_current_span("mcp_server.cold_start") as cs_span:
                cs_span.set_attribute("mcp.server.id", target_server_id)
                try:
                    self._single_flight.do(
                        target_server_id,
                        lambda: ctx.command_bus.send(StartMcpServerCommand(mcp_server_id=target_server_id)),
                    )
                    cs_span.set_attribute("cold_start.result", "success")
                except Exception as e:  # noqa: BLE001 -- fault-barrier: mcp_server start failure must return error result, not crash batch
                    cs_span.set_attribute("cold_start.result", "error")
                    cs_span.record_exception(e)
                    return CallResult(
                        index=call.index,
                        call_id=call.call_id,
                        success=False,
                        error=f"Failed to start mcp_server: {e}",
                        error_type="McpServerStartError",
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

        # Acquire concurrency slots (global + per-mcp_server) before invocation.
        # This is where backpressure happens: if the global or mcp_server semaphore
        # is full, this thread blocks until a slot frees up. Crucially, the call
        # starts as soon as ANY slot is freed -- it does not wait for an entire
        # batch wave to complete (unlike sequential chunking).
        cm = self.concurrency_manager
        with tracer.start_as_current_span("concurrency.acquire") as conc_span:
            conc_span.set_attribute("mcp.server.id", call.mcp_server)
            with cm.acquire(call.mcp_server) as wait_s:
                conc_span.set_attribute("concurrency.wait_ms", round(wait_s * 1000, 2))
                if wait_s > 0.01:
                    logger.debug(
                        "concurrency_slot_wait",
                        call_id=call.call_id,
                        mcp_server=call.mcp_server,
                        wait_ms=round(wait_s * 1000, 2),
                    )

                result = self._invoke_with_retry(
                    call, cancel_event, effective_timeout, call_start, ctx, target_server_id
                )

        # Feed the group health tracker so its circuit-breaker and member rotation
        # react to actual invoke outcomes (enables failover on the call path, #275).
        if is_group and group_obj is not None:
            if result.success:
                group_obj.report_success(target_server_id)
            else:
                group_obj.report_failure(target_server_id)
        return result

    def _invoke_with_retry(
        self,
        call: CallSpec,
        cancel_event: threading.Event,
        effective_timeout: float,
        call_start: float,
        ctx: Any,
        target_server_id: str | None = None,
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

        # Dispatch to the resolved target: the selected group member when
        # call.mcp_server is a group, otherwise the server itself.
        dispatch_server_id = target_server_id or call.mcp_server

        def do_invoke() -> dict[str, Any]:
            with tracer.start_as_current_span("command.send.InvokeToolCommand") as cmd_span:
                cmd_span.set_attribute("mcp.server.id", dispatch_server_id)
                cmd_span.set_attribute("mcp.tool.name", call.tool)
                cmd_span.set_attribute("command.timeout", effective_timeout)
                command = InvokeToolCommand(
                    mcp_server_id=dispatch_server_id,
                    tool_name=call.tool,
                    arguments=call.arguments,
                    timeout=effective_timeout,
                )
                result = ctx.command_bus.send(command)
                cmd_span.set_attribute("command.result", "success")
                return cast(dict[str, Any], result)

        # Execute with retry if max_retries > 1
        retry_result: RetryResult | None = None
        if call.max_retries > 1:
            with tracer.start_as_current_span("invoke_with_retry") as retry_span:
                retry_span.set_attribute("retry.max_attempts", call.max_retries)
                retry_span.set_attribute("mcp.server.id", call.mcp_server)
                retry_span.set_attribute("mcp.tool.name", call.tool)
                policy = RetryPolicy(max_attempts=call.max_retries)
                retry_result = retry_sync(
                    operation=do_invoke,
                    policy=policy,
                    mcp_server=call.mcp_server,
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
                    mcp_server=call.mcp_server,
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
                    mcp_server=call.mcp_server,
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
                mcp_server=call.mcp_server,
                tool=call.tool,
                size_bytes=result_size,
                limit_bytes=MAX_RESPONSE_SIZE_BYTES,
            )

        logger.debug(
            "batch_call_completed",
            call_id=call.call_id,
            mcp_server=call.mcp_server,
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
