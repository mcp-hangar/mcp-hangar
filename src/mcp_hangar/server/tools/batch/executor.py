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
from dataclasses import dataclass
import json
import threading
import time
from typing import Any, cast, Literal


from ....application.commands import InvokeToolCommand, StartMcpServerCommand
from ....application.services.mutator_pipeline import MutatorPipeline
from ....application.tasks.tool_pin_context import CurrentToolPin, get_current_tool_pin, set_current_tool_pin
from ....application.services.validator_pipeline import ValidatorPipeline
from ....domain.contracts.mutator import MutationContext
from ....domain.contracts.validator import ValidationContext
from ....domain.events import (
    BatchCallCompleted,
    BatchInvocationCompleted,
    BatchInvocationRequested,
    ToolWithdrawnRejected,
)
from ....context import bind_routing_headers, get_identity_context
from ....application.read_models.tool_projection import get_tool_projection_registry
from ....domain.services import get_tool_access_resolver
from ....domain.services.digest_validator import DigestValidator
from ....domain.value_objects import DigestEnforcement, DigestPolicy, DigestUnknownPolicy
from ....infrastructure.single_flight import SingleFlight
from ....logging_config import get_logger
from ....observability.tracing import extract_trace_context, get_tracer, mark_span_error
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
from ....negotiation import read_protocol_negotiation, set_current_protocol_negotiation
from ....retry import retry_sync, RetryPolicy, RetryResult
from ...context import get_context
from ...state import GROUPS
from .concurrency import ConcurrencyManager, get_concurrency_manager
from .models import BatchResult, CallResult, CallSpec, MAX_RESPONSE_SIZE_BYTES, RelayCapture, RetryMetadata

logger = get_logger(__name__)


def _inbound_trace_meta(ctx: Any) -> dict[str, str]:
    """Read SEP-414 trace keys from the inbound request's ``params._meta``.

    Returns only ``traceparent``/``tracestate`` (``baggage`` is deliberately
    excluded pending cross-tenant scrubbing). Best-effort fault barrier: trace
    context is a convention (SEP-414 MAY), so any failure to read it returns
    ``{}`` and never breaks the call.
    """
    try:
        req_meta = ctx.request_context.meta
        if req_meta is None:
            return {}
        dumped = req_meta.model_dump(exclude_none=True) if hasattr(req_meta, "model_dump") else dict(req_meta)
        return {k: str(v) for k, v in dumped.items() if k in ("traceparent", "tracestate") and isinstance(v, str)}
    except Exception:  # noqa: BLE001 -- fault barrier: trace reading must not break invocation
        return {}


def _inbound_meta_dict(ctx: Any) -> dict[str, Any] | None:
    """Return the inbound request's ``params._meta`` as a plain dict, or ``None``.

    Best-effort fault barrier mirroring ``_inbound_trace_meta``: pydantic ``Meta``
    models are dumped, plain mappings are copied, and any failure yields ``None``
    so a missing/malformed ``_meta`` never breaks the call.
    """
    try:
        req_meta = ctx.request_context.meta
        if req_meta is None:
            return None
        if hasattr(req_meta, "model_dump"):
            return dict(req_meta.model_dump(exclude_none=True))
        return dict(req_meta)
    except Exception:  # noqa: BLE001 -- fault barrier: meta reading must not break invocation
        return None


def _is_task_result(result: dict[str, Any]) -> bool:
    """Return True if an upstream ``tools/call`` result is an MCP task handle.

    An ``mcp.types.CreateTaskResult`` carries a ``task`` object (a ``Task`` with
    ``taskId``/``status``) and NO ``content`` -- distinct from a normal
    ``CallToolResult`` which carries ``content``. So the upstream result is a
    task result iff it contains a ``task`` object bearing a task id or status.

    Defensive: accepts an arbitrary dict, tolerates a non-dict ``task`` value or
    a malformed shape, and only returns True for the task-handle shape.
    """
    if not isinstance(result, dict):
        return False
    task = result.get("task")
    if not isinstance(task, dict):
        return False
    return any(key in task for key in ("taskId", "task_id", "id", "status"))


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


#: Sentinel: "not looked up yet", distinct from a genuine "no such projection".
_UNRESOLVED = object()


@dataclass
class _CallPipeline:
    """Mutable state threaded through the gates of a single batch call.

    A shared object rather than a widening parameter list: the later gates need
    what the earlier ones resolved -- the selected group member, the tool
    projection, the tenant's digest pin -- and passing eleven values down a
    chain of eleven methods is how the 454-line function this replaced came to
    be one function.
    """

    call: CallSpec
    ctx: Any
    call_start: float
    cancel_event: threading.Event
    global_timeout: float
    batch_start_time: float
    caller_tenant_id: str | None
    resolver: Any
    proj_registry: Any
    tracer: Any

    #: Set by _gate_global_timeout: what is left of the batch budget.
    effective_timeout: float = 0.0
    #: Set by _gate_resolve_target.
    mcp_server_obj: Any = None
    is_group: bool = False
    group_obj: Any = None
    target_server_id: str = ""
    #: Set by _gate_digest_pin.
    pin: Any = None
    _projection: Any = _UNRESOLVED
    #: True when a pin exists but the catalogue was not there to check it
    #: against; the cold start populates it and the gate re-runs (#601).
    digest_pin_deferred: bool = False

    @property
    def projection(self) -> Any:
        """The tool's projection, resolved once and cached.

        A cached lookup rather than a field set by whichever gate happens to run
        first: both the withdrawal gate and the digest-pin gate need it, and
        making one of them responsible for populating it for the other is an
        ordering dependency that fails silently -- reorder the two and the pin
        check quietly defers instead of running.

        Two ids, because a group has two names (#1040). ``call.mcp_server`` is
        the GROUP id whenever a group is the target -- front_door collapses the
        member so selection stays with the group's strategy, and an egress caller
        names the group directly -- while the registry is keyed by the id that
        STARTED, which is always a member. Resolving only the group id returned
        ``None``, and ``None`` means "unknown tool, do not block": the withdrawal
        gate waved every group-routed call through and the pin gate returned
        before checking anything. The group id is asked first because a
        group-declared withdrawal is the narrower statement (it covers the group
        however a member is selected); the selected member answers otherwise, and
        is also where the discovered schema the pin is validated against lives.
        """
        if self._projection is _UNRESOLVED:
            resolved = self.proj_registry.resolve(self.call.mcp_server, self.call.tool, self.caller_tenant_id)
            if resolved is None and self.target_server_id and self.target_server_id != self.call.mcp_server:
                resolved = self.proj_registry.resolve(self.target_server_id, self.call.tool, self.caller_tenant_id)
            self._projection = resolved
        return self._projection

    def reresolve_projection(self) -> Any:
        """Look the projection up again, after a cold start populated it.

        The deferred pin gate needs the answer to a question that had none when
        the cached one was taken. It goes through the property rather than
        calling the registry itself, because a second copy of the two-name
        lookup is a copy that can be missing the fallback -- which is what it
        was: the deferred gate asked the group id alone, found nothing for a
        member that had just started, and refused the first pinned call after
        every gateway boot as unverifiable (#1166).
        """
        self._projection = _UNRESOLVED
        return self.projection

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.call_start) * 1000

    def refuse(self, error: str, error_type: str) -> CallResult:
        """A refusal for this call, timed from its start.

        Every gate built this by hand, eight lines apiece, and the elapsed_ms
        argument is the one an edit forgets.
        """
        return CallResult(
            index=self.call.index,
            call_id=self.call.call_id,
            success=False,
            error=error,
            error_type=error_type,
            elapsed_ms=self.elapsed_ms(),
        )


def _log_call_failure(call: Any, error: Any, error_type: str, elapsed_ms: float) -> None:
    """Log a failed call, loudly when the failure was a deliberate refusal.

    A policy refusal and an upstream blowing up are not the same class of event
    and were logged the same way: `logger.debug`, which a default deployment
    does not emit, carrying `str(e)` -- the generic caller-facing message, with
    the reason the policy computed left behind in `.details` where only the REST
    middleware ever looked (#1128).

    The refusal is an enforcement decision this gateway made on purpose, so it
    is a warning and it says why. Everything else keeps the debug level: an
    upstream failure is already reported to the caller in the CallResult, and
    raising it here would make a batch of failing calls a log flood.
    """
    details = getattr(error, "details", None)
    reason = details.get("reason") if isinstance(details, dict) else None
    refused = error_type in ("EgressPolicyDeniedError", "EgressPolicyApprovalRequiredError")
    log = logger.warning if refused else logger.debug
    log(
        "batch_call_refused" if refused else "batch_call_failed",
        call_id=call.call_id,
        mcp_server=call.mcp_server,
        tool=call.tool,
        error=str(error),
        error_type=error_type,
        reason=reason,
        policy_id=getattr(error, "policy_id", None),
        elapsed_ms=round(elapsed_ms, 2),
    )


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

    def __init__(
        self,
        concurrency_manager: ConcurrencyManager | None = None,
        validator_pipeline: ValidatorPipeline | None = None,
        mutator_pipeline: MutatorPipeline | None = None,
    ):
        self._single_flight = SingleFlight(cache_results=False)
        self._active_batches = 0
        self._active_lock = threading.Lock()
        self._concurrency_manager = concurrency_manager
        # Interceptor validator pipeline. Defaults to a fresh EMPTY pipeline
        # (no validators registered), so it always allows -- preserving current
        # behavior. Fail-closed only takes effect once validators are registered.
        self._validator_pipeline = validator_pipeline if validator_pipeline is not None else ValidatorPipeline()
        # Interceptor mutator pipeline. Defaults to a fresh EMPTY pipeline (no
        # mutators registered), so payloads pass through unchanged -- preserving
        # current behavior. Transforms only take effect once mutators are registered.
        self._mutator_pipeline = mutator_pipeline if mutator_pipeline is not None else MutatorPipeline()

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

    def _l7_approval_rule(self, call: CallSpec, ctx: Any) -> str | None:
        """The L7 (MCPEgressPolicy) requireApproval verdict for this call.

        Returns the human-readable reason when the target server's enforced L7
        policy routes this tool to approval (#921), else None. Audit mode
        observes and never blocks, so it never asks a human; deny needs no
        gate -- the aggregate refuses it on invoke.
        """
        try:
            server = ctx.repository.get(call.mcp_server)
        except Exception:  # noqa: BLE001 -- resolution problems belong to the invoke path's own errors
            return None
        policy = getattr(server, "l7_policy", None)
        if policy is None:
            return None

        from mcp_hangar.context import get_routing_headers
        from mcp_hangar.domain.policies.egress_l7 import PolicyMode, ToolAction, evaluate

        if policy.mode is not PolicyMode.ENFORCE:
            return None
        # Same inputs as the aggregate's own evaluation (#1058): a header
        # selector that routes a call to approval there must route it here, or
        # the gate is skipped and the aggregate refuses instead of asking.
        decision = evaluate(call.tool, call.arguments or {}, policy, get_routing_headers())
        if decision.action is ToolAction.REQUIRE_APPROVAL:
            return "; ".join(decision.reasons) or "matched a requireApproval rule"
        return None

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
        # Cleared per call: worker threads are reused across calls, so a stale
        # id from the previous call in this thread must never be revalidated
        # against the current one.
        _approval_loop_local.approval_id = None

        # Get effective policy for this mcp_server (or fallback to _global)
        policy = resolver.resolve_effective_policy(call.mcp_server)
        if policy.is_unrestricted():
            # Check global policy fallback
            policy = resolver.resolve_effective_policy("_global")

        needs_mrtr_approval = (not policy.is_unrestricted()) and policy.requires_approval(call.tool)

        # The L7 egress policy is the second, independent source of "ask a
        # human" (#921): before this, its requireApproval verdict failed
        # closed in the aggregate and was indistinguishable from deny.
        l7_rule = self._l7_approval_rule(call, ctx)

        if not needs_mrtr_approval and l7_rule is None:
            return None

        # Tool requires approval -- delegate to ApprovalGateService
        gate_service = getattr(ctx, "approval_gate", None)
        if gate_service is None:
            if l7_rule is not None:
                # An L7 requireApproval with nobody to ask stays fail-closed:
                # the aggregate raises EgressPolicyApprovalRequiredError on
                # invoke, exactly as before this wiring. Do NOT return a pass.
                logger.info("approval_gate_not_configured_l7_fails_closed", tool=call.tool, rule=l7_rule)
                return None
            logger.debug("approval_gate_not_configured", tool=call.tool)
            return None

        if not needs_mrtr_approval:
            # L7-only: hand the gate a policy that says exactly what the
            # egress policy said -- this one tool needs a human. Timeout and
            # channel fall back to the deployment defaults the gate already
            # applies for an empty channel.
            from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy

            policy = ToolAccessPolicy(approval_list=(call.tool,))
            logger.info("egress_policy_approval_routing", tool=call.tool, rule=l7_rule)

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
            # Bind the caller's tenant and identity onto the approval so the
            # resolve/list surfaces can be scoped to them. Without this, an
            # approver in one tenant can see and resolve another tenant's
            # approvals, because authorization is by permission alone.
            _ident = get_identity_context()
            _caller = _ident.caller if _ident is not None else None
            _tenant_id = _caller.tenant_id if _caller is not None else None
            _requested_by = (_caller.user_id or _caller.agent_id) if _caller is not None else None

            thread_loop = _get_approval_loop()
            result = thread_loop.run_until_complete(
                gate_service.check(
                    mcp_server_id=call.mcp_server,
                    tool_name=call.tool,
                    arguments=call.arguments,
                    policy=policy,
                    correlation_id=call.call_id,
                    tenant_id=_tenant_id,
                    requested_by=_requested_by,
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
            _approval_loop_local.approval_id = None
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

        # Approved -- continue execution. The caller revalidates before dispatch;
        # see _revalidate_approval.
        _approval_loop_local.approval_id = result.approval_id
        return None

    def _revalidate_after_hold(
        self,
        call: CallSpec,
        resolver: Any,
        ctx: Any,
        approval_id: str,
        pin: Any,
        proj_registry: Any,
        caller_tenant_id: Any,
        enforce_digest_pin: Any,
        *,
        group_id: str | None = None,
        target_server_id: str = "",
    ) -> CallResult | None:
        """Re-check, after an approval hold, everything decided before it.

        Returns a refusal ``CallResult`` when the approved call may no longer
        run, or ``None`` to proceed.

        Args:
            group_id: The group the call targets, if any -- the same value
                ``_gate_tool_access`` passes. Without it (#1039) this asked the
                resolver a different question than the pre-hold gate did: a
                group's policy was never merged, so a deny added to a group
                during the hold did not refuse the approved call.
            target_server_id: The member a group selected, for the projection
                and pin re-resolve (#1040).
        """

        def _refuse(reason: str, code: str) -> CallResult:
            logger.warning(
                "approval_revalidation_failed",
                approval_id=approval_id,
                mcp_server=call.mcp_server,
                tool=call.tool,
                reason=reason,
            )
            return CallResult(
                index=call.index,
                call_id=call.call_id,
                success=False,
                error=f"Approval no longer valid at dispatch: {reason}",
                error_type=code,
                elapsed_ms=0,
            )

        # The record itself: still approved, still inside its window, and still
        # describing these arguments.
        gate_service = getattr(ctx, "approval_gate", None)
        if gate_service is not None and hasattr(gate_service, "revalidate"):
            try:
                reason = _get_approval_loop().run_until_complete(
                    gate_service.revalidate(approval_id, call.arguments or {})
                )
            except (RuntimeError, OSError, ValueError, TimeoutError) as exc:
                # Fail closed: an approval we cannot re-verify is not an
                # approval we can act on.
                return _refuse(f"revalidation error: {exc}", "ApprovalRevalidationError")
            if reason is not None:
                return _refuse(reason, "ApprovalNoLongerValid")

        # Effective policy, re-resolved. A tool moved to deny during the hold
        # must not execute on the pre-change decision.
        try:
            # The caller's tenant and the target group are carried, not dropped:
            # asked without them this was a different question than the pre-hold
            # gate asked, and in front_door a resolve with no member_id is the
            # fail-closed missing-identity branch, which refused EVERY approved
            # call at dispatch (#1039).
            #
            # No `_global` second lookup: `_compute_effective_policy` merges the
            # `_global` policy into every scope it resolves, so a result that is
            # unrestricted means `_global` was empty too -- the fallback could
            # only ever re-answer the same question.
            policy = resolver.resolve_effective_policy(
                call.mcp_server,
                group_id,
                caller_tenant_id,
                member_server_id=target_server_id or None,
            )
            if not policy.is_unrestricted() and not policy.is_tool_allowed(call.tool):
                return _refuse("tool is no longer allowed by policy", "ToolAccessDenied")
        except Exception as exc:  # noqa: BLE001 -- fail closed on an unreadable policy
            return _refuse(f"policy could not be re-resolved: {exc}", "ApprovalRevalidationError")

        # The pinned tool digest, re-verified against the catalogue as it is
        # now. The pre-gate check spoke for a schema that may since have moved.
        if pin is not None:
            projection = proj_registry.resolve(call.mcp_server, call.tool, caller_tenant_id)
            if projection is None and target_server_id and target_server_id != call.mcp_server:
                projection = proj_registry.resolve(target_server_id, call.tool, caller_tenant_id)
            if projection is not None:
                rejection: CallResult | None = enforce_digest_pin(projection, pin)
                if rejection is not None:
                    return rejection

        return None

    def _check_validators(self, call: CallSpec) -> CallResult | None:
        """Run the interceptor ValidatorPipeline against this tool call.

        Fail-closed but behavior-preserving: with the default empty pipeline no
        validators run, so this always returns None (proceed). Once validators
        are registered, an enforced denial short-circuits the call BEFORE the
        approval gate and invoke.

        Returns None if the call is allowed (continue execution). Returns a
        CallResult if a validator denied the call.
        """
        ctx = ValidationContext(
            method="tools/call",
            direction="request",
            payload={"name": call.tool, "arguments": call.arguments or {}},
            correlation_id=call.call_id,
        )
        result = self._validator_pipeline.execute(ctx)
        if not result.allowed:
            return CallResult(
                index=call.index,
                call_id=call.call_id,
                success=False,
                error=result.reason or "Denied by validator",
                error_type="ValidatorDenied",
                elapsed_ms=0,
            )
        return None

    def _mutate(
        self,
        method: str,
        direction: Literal["request", "response"],
        payload: dict[str, Any],
        correlation_id: str,
    ) -> dict[str, Any]:
        """Run the interceptor MutatorPipeline over a tool-call payload.

        Behavior-preserving: with the default empty pipeline no mutators run, so
        the payload is returned unchanged. Once mutators are registered, the
        applicable ones transform the payload in priority order and the
        (possibly changed) payload is returned.
        """
        ctx = MutationContext(
            method=method,
            direction=direction,
            payload=payload,
            correlation_id=correlation_id,
        )
        result = self._mutator_pipeline.execute(ctx)
        return result.payload

    def execute(  # noqa: C901 -- baseline CC=17; split before extending
        self,
        batch_id: str,
        calls: list[CallSpec],
        max_concurrency: int,
        global_timeout: float,
        fail_fast: bool,
        request_ctx: Any | None = None,
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
            request_ctx: The real FastMCP request ``Context`` (when invoked over an
                MCP transport), used solely to read the inbound ``params._meta``
                for trace context and protocol negotiation. ``None`` on the
                stdio / no-request path, in which case both default (empty trace /
                supported protocol version) exactly as before. Distinct from the
                ApplicationContext returned by ``get_context()``, which has no
                ``request_context`` and is still used for the event/command buses.

        Returns:
            BatchResult with all call results.
        """
        ctx = get_context()

        # Stateless negotiation (SEP-2575): the client conveys its protocolVersion
        # and capabilities per request in params._meta (no initialize handshake).
        # Read them once at ingress and publish to a request-scoped contextvar that
        # batch worker threads inherit via copy_context(). Additive: no gating here.
        # Over streamable-HTTP the inbound _meta lives on the FastMCP request_ctx
        # (the ApplicationContext has no request_context), so read from request_ctx;
        # when it is None (stdio / no request) the helper yields None and negotiation
        # falls back to the default supported version -- unchanged behavior.
        set_current_protocol_negotiation(read_protocol_negotiation(_inbound_meta_dict(request_ctx)))

        # The same request's SEP-2243 routing headers, for an L7 policy that
        # selects on Mcp-Param-* (#1058). Bound here rather than only on the
        # front door so a selector is never silently inert on this surface --
        # a policy that reports enforcing while a rule cannot fire is the
        # failure this module already refuses for secret-pattern groups.
        bind_routing_headers(request_ctx)

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
                            request_ctx,
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
        request_ctx: Any | None = None,
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
            request_ctx: The real FastMCP request ``Context`` (or ``None`` on the
                stdio / no-request path), used to read the inbound ``params._meta``
                for W3C trace context. Distinct from the ApplicationContext returned
                by ``get_context()``.

        Returns:
            CallResult for this call.
        """
        ctx = get_context()
        call_start = time.perf_counter()

        # Extract W3C TraceContext for distributed tracing. Per SEP-414 it travels
        # in the inbound request's params._meta (un-prefixed traceparent/tracestate);
        # fall back to the legacy call.metadata field. _meta wins when both present.
        # The inbound _meta lives on the FastMCP request_ctx (the ApplicationContext
        # has no request_context); when request_ctx is None the helper yields {} and
        # only call.metadata is used -- the pre-bridge default, unchanged.
        metadata = call.metadata or {}
        parent_context = extract_trace_context({**metadata, **_inbound_trace_meta(request_ctx)})

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
            span.set_attribute("gen_ai.tool.name", call.tool)
            span.set_attribute("batch.call.id", call.call_id)
            result = self._execute_call_inner(
                call,
                cancel_event,
                global_timeout,
                batch_start_time,
                ctx,
                call_start,
            )
            # The inner call handles failures as data (CallResult), so the span
            # never sees an exception. Mark it ERROR explicitly so failing tool
            # calls are filterable as error traces instead of looking successful.
            if not result.success:
                mark_span_error(span, result.error)
            return result

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

        The body is a chain of gates. Each returns a ``CallResult`` to refuse the
        call or ``None`` to hand it to the next, and they share the mutable
        ``_CallPipeline`` below because the later ones need what the earlier ones
        resolved -- the selected group member, the tool projection, the tenant's
        digest pin.

        Their ORDER is load-bearing rather than incidental: the refusal a caller
        receives decides what it does next, so swapping two gates silently
        changes the answer. ``_GATES`` is that order, and
        tests/unit/test_batch_gate_precedence.py arranges pairs of them to fail
        at once and asserts which one wins.
        """
        pipeline = _CallPipeline(
            call=call,
            ctx=ctx,
            call_start=call_start,
            cancel_event=cancel_event,
            global_timeout=global_timeout,
            batch_start_time=batch_start_time,
            # Read the caller's tenant first: a group's member selection may be
            # tenant-aware (per-tenant canary / version routing, #275). The
            # identity is set by IdentityMiddleware and carried into this worker
            # thread via copy_context() (PR #239).
            caller_tenant_id=(identity.caller.tenant_id if (identity := get_identity_context()) is not None else None),
            resolver=get_tool_access_resolver(),
            proj_registry=get_tool_projection_registry(),
            tracer=get_tracer(__name__),
        )

        for gate in _GATES:
            refusal = gate(self, pipeline)
            if refusal is not None:
                return refusal

        # Acquire concurrency slots (global + per-mcp_server) before invocation.
        # This is where backpressure happens: if the global or mcp_server semaphore
        # is full, this thread blocks until a slot frees up. Crucially, the call
        # starts as soon as ANY slot is freed -- it does not wait for an entire
        # batch wave to complete (unlike sequential chunking).
        cm = self.concurrency_manager
        with pipeline.tracer.start_as_current_span("concurrency.acquire") as conc_span:
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
                    call,
                    cancel_event,
                    pipeline.effective_timeout,
                    call_start,
                    ctx,
                    pipeline.target_server_id,
                )

        relayed = self._relay_upstream_task(pipeline, result)
        if relayed is not None:
            return relayed

        # Feed the group health tracker so its circuit-breaker and member rotation
        # react to actual invoke outcomes (enables failover on the call path, #275).
        if pipeline.is_group and pipeline.group_obj is not None:
            if result.success:
                pipeline.group_obj.report_success(pipeline.target_server_id)
            else:
                pipeline.group_obj.report_failure(pipeline.target_server_id)
        return result

    # -- gates ---------------------------------------------------------------
    #
    # Each returns None to let the call through, or a CallResult to refuse it.
    # Registered in _GATES at the bottom of this module, which is the order they
    # run in.

    def _gate_cancelled_before_execution(self, p: "_CallPipeline") -> CallResult | None:
        if p.cancel_event.is_set():
            # elapsed_ms is 0.0 rather than measured: nothing ran.
            return CallResult(
                index=p.call.index,
                call_id=p.call.call_id,
                success=False,
                error="Cancelled before execution",
                error_type="CancellationError",
                elapsed_ms=0.0,
            )
        return None

    def _gate_global_timeout(self, p: "_CallPipeline") -> CallResult | None:
        """Refuse if the batch's budget is already spent, and set what is left."""
        remaining_global = p.global_timeout - (time.perf_counter() - p.batch_start_time)
        if remaining_global <= 0:
            return CallResult(
                index=p.call.index,
                call_id=p.call.call_id,
                success=False,
                error="Global timeout exceeded",
                error_type="TimeoutError",
                elapsed_ms=0.0,
            )
        p.effective_timeout = min(p.call.timeout, remaining_global) if p.call.timeout is not None else remaining_global
        return None

    def _gate_resolve_target(self, p: "_CallPipeline") -> CallResult | None:
        """Resolve the call to a concrete backend, selecting a group member if needed.

        For a group the member is selected NOW (tenant-aware when a canary policy
        is set) so the rest of the pipeline -- cold-start, circuit breaker,
        dispatch -- targets a real backend. Policy, withdrawal and digest-pin
        checks below still key on the logical group id.
        """
        p.mcp_server_obj = p.ctx.get_mcp_server(p.call.mcp_server)
        p.target_server_id = p.call.mcp_server
        if p.mcp_server_obj:
            return None

        p.group_obj = GROUPS.get(p.call.mcp_server)
        if p.group_obj:
            p.is_group = True
            selected_member = p.group_obj.select_member_for(p.caller_tenant_id)
            if selected_member is None:
                return p.refuse(f"No available member in group '{p.call.mcp_server}'", "NoAvailableMemberError")
            p.mcp_server_obj = selected_member
            p.target_server_id = selected_member.id.value
        elif not p.ctx.mcp_server_exists(p.call.mcp_server):
            return p.refuse(f"McpServer '{p.call.mcp_server}' not found", "McpServerNotFoundError")
        return None

    def _gate_tool_access(self, p: "_CallPipeline") -> CallResult | None:
        """Tool access policy, checked BEFORE starting the server or executing."""
        with p.tracer.start_as_current_span("policy.check_access") as policy_span:
            policy_span.set_attribute("mcp.server.id", p.call.mcp_server)
            policy_span.set_attribute("gen_ai.tool.name", p.call.tool)
            policy_span.set_attribute("policy.is_group", p.is_group)
            if p.is_group:
                p.group_obj = GROUPS.get(p.call.mcp_server)
                # Group policy AND the policy of the member `_gate_resolve_target`
                # just selected. The member half is keyed by the member SERVER id,
                # so passing only the tenant resolved to group-level alone and a
                # member deny_list never reached the verdict (#1164).
                allowed = p.resolver.is_tool_allowed(
                    mcp_server_id=p.call.mcp_server,
                    tool_name=p.call.tool,
                    group_id=p.call.mcp_server,
                    member_id=p.caller_tenant_id,
                    member_server_id=p.target_server_id or None,
                )
            else:
                # For standalone mcp_servers: server->member merge when tenant is known
                allowed = p.resolver.is_tool_allowed(
                    mcp_server_id=p.call.mcp_server,
                    tool_name=p.call.tool,
                    member_id=p.caller_tenant_id,
                )
            policy_span.set_attribute("policy.allowed", allowed)

        if allowed:
            return None
        logger.info(
            "tool_access_denied",
            mcp_server_id=p.call.mcp_server,
            tool=p.call.tool,
            reason="tool_not_in_access_policy",
        )
        TOOL_ACCESS_DENIED_TOTAL.inc(mcp_server=p.call.mcp_server, tool=p.call.tool, reason="tool_not_in_access_policy")
        return p.refuse("Tool not available for this mcp_server", "ToolAccessDeniedError")

    def _gate_withdrawal(self, p: "_CallPipeline") -> CallResult | None:
        """Tool withdrawal status, checked BEFORE backend invoke (#231).

        Guarantee: per-process-after-reload (registry is config-reload-driven;
        runtime mutation is #235). Rejection is envelope-level; protocol-clean
        -32601 is #232. Semantics: projection is None -> registry unpopulated ->
        do NOT block (safe default). Only an explicit is_withdrawn_for() == True
        causes rejection.
        """
        projection = p.projection
        if projection is None or not projection.is_withdrawn_for(p.caller_tenant_id):
            return None
        logger.info(
            "tool_withdrawn_rejected",
            mcp_server_id=p.call.mcp_server,
            tool=p.call.tool,
            tenant_id=p.caller_tenant_id,
        )
        p.ctx.event_bus.publish(
            ToolWithdrawnRejected(tenant_id=p.caller_tenant_id, mcp_server=p.call.mcp_server, tool=p.call.tool)
        )
        return p.refuse(f"Tool '{p.call.tool}' is withdrawn for this tenant", "ToolWithdrawnError")

    def _enforce_digest_pin(self, p: "_CallPipeline", projection: Any, pin: Any) -> CallResult | None:
        """Validate *projection* against the tenant's *pin*; a CallResult means reject."""
        enforcement = p.proj_registry.digest_enforcement(p.call.mcp_server)
        try:
            digest_result = DigestValidator(
                DigestPolicy(
                    enforcement=enforcement,
                    unknown=DigestUnknownPolicy.BLOCK,
                    allowlist=frozenset({pin}),
                )
            ).validate_tool(projection.schema, p.call.mcp_server, p.call.call_id, tenant_id=p.caller_tenant_id)
            blocked = digest_result.blocked
            event = digest_result.event
        except Exception:  # noqa: BLE001 -- a malformed projection schema must not 500 the call path
            # Cannot compute/verify the digest: fail closed under block, else allow.
            logger.warning(
                "tool_digest_pin_unverifiable",
                mcp_server_id=p.call.mcp_server,
                tool=p.call.tool,
                tenant_id=p.caller_tenant_id,
            )
            blocked = enforcement == DigestEnforcement.BLOCK
            event = None
        if event is not None:
            p.ctx.event_bus.publish(event)
        if blocked:
            logger.info(
                "tool_digest_pin_rejected",
                mcp_server_id=p.call.mcp_server,
                tool=p.call.tool,
                tenant_id=p.caller_tenant_id,
            )
            # "for this tenant" was true while a pin could only be declared for
            # one, and became a small lie once an all-tenants pin could refuse a
            # caller who carries no tenant at all (#902).
            return p.refuse(
                f"Tool '{p.call.tool}' schema does not match its pinned digest",
                "ToolDigestMismatchError",
            )
        # Pin verified: bind the tool's approved digest to the request context so
        # that if this call is task-augmented and returns a task handle,
        # GovernedTaskStore.create_task pins the task to this digest and
        # re-verifies it fail-closed on result retrieval (#320). Each batch call
        # runs in its own contextvars.copy_context() (see execute()), so this set
        # is confined to the current call.
        set_current_tool_pin(
            CurrentToolPin(mcp_server=p.call.mcp_server, tool_name=p.call.tool, pinned_digest=pin.sha256)
        )
        return None

    def _gate_digest_pin(self, p: "_CallPipeline") -> CallResult | None:
        """Per-tenant digest pin enforcement (#233).

        If the caller's tenant pinned this tool to an approved digest, validate
        the backend's current schema against it and enforce per the server's
        configured mode. No pin -> unchanged behavior.

        NOTE: the withdrawal check above takes precedence -- a withdrawn tool is
        rejected before reaching here, so no mismatch event fires for a tool that
        is both withdrawn and pinned.

        A pinned tool whose projection is not in the registry yet cannot be
        checked here. That is the state of every backend that has not started in
        this process: the catalogue is populated by the McpServerStarted handler,
        and the cold start happens LATER in this pipeline. Left as-is, the first
        call after a gateway boot skipped the pin entirely -- one unvalidated
        call per boot per server, and gateway restarts are routine in Kubernetes
        (#601). So the check is deferred and re-run by
        _gate_deferred_digest_pin once the cold start has populated the
        catalogue.
        """
        p.pin = p.proj_registry.resolve_pin(p.call.mcp_server, p.call.tool, p.caller_tenant_id)
        if p.pin is None and p.target_server_id and p.target_server_id != p.call.mcp_server:
            # A pin declared on the member a group selected. Same two-name
            # problem as the projection above (#1040): without this, a pinned
            # tool served through a group was never validated against its pin,
            # in either topology and with no listing filter behind it.
            p.pin = p.proj_registry.resolve_pin(p.target_server_id, p.call.tool, p.caller_tenant_id)
        if p.pin is None:
            return None
        if p.projection is None:
            p.digest_pin_deferred = True
            return None
        return self._enforce_digest_pin(p, p.projection, p.pin)

    def _gate_circuit_breaker(self, p: "_CallPipeline") -> CallResult | None:
        """Circuit breaker / health degradation of the resolved target."""
        if not p.mcp_server_obj:
            return None
        if not (hasattr(p.mcp_server_obj, "health") and p.mcp_server_obj.health.should_degrade()):
            return None
        BATCH_CIRCUIT_BREAKER_REJECTIONS_TOTAL.inc(mcp_server=p.target_server_id)
        return p.refuse("Circuit breaker open (too many consecutive failures)", "CircuitBreakerOpen")

    def _gate_validators(self, p: "_CallPipeline") -> CallResult | None:
        """Interceptor validators, fail-closed BEFORE prompting for approval.

        Ordered ahead of the approval gate so a validator denial short-circuits
        without blocking on a human decision. Empty pipeline (default) allows.
        """
        denied = self._check_validators(p.call)
        if denied is None:
            return None
        denied.elapsed_ms = p.elapsed_ms()
        return denied

    def _gate_approval(self, p: "_CallPipeline") -> CallResult | None:
        """Human approval gate, plus the re-check of everything it paused.

        The policy is configured via the server config and applied to the
        ToolAccessResolver; this uses the resolver's effective policy
        (mcp_server-specific or _global fallback).
        """
        with p.tracer.start_as_current_span("approval_gate.check") as approval_span:
            approval_span.set_attribute("mcp.server.id", p.call.mcp_server)
            approval_span.set_attribute("gen_ai.tool.name", p.call.tool)
            approval_result = self._check_approval_gate(p.call, p.resolver, p.ctx)
            if approval_result is not None:
                approval_span.set_attribute("approval.result", approval_result.error_type or "denied")
                approval_result.elapsed_ms = p.elapsed_ms()
                return approval_result

            # Re-establish validity after the hold. The gate blocks for up to
            # `approval_timeout_seconds` (300 by default), and every check that
            # preceded it -- effective policy, tool withdrawal, the pinned tool
            # digest -- was evaluated against the world as it was *before* that
            # pause. Config reload is a supported live operation, so withdrawing
            # a tool or tightening a policy while a decision is pending left the
            # held call to dispatch on the superseded decision.
            granted_id = getattr(_approval_loop_local, "approval_id", None)
            if granted_id is not None:
                refusal = self._revalidate_after_hold(
                    p.call,
                    p.resolver,
                    p.ctx,
                    granted_id,
                    p.pin,
                    p.proj_registry,
                    p.caller_tenant_id,
                    lambda projection, pin: self._enforce_digest_pin(p, projection, pin),
                    group_id=p.call.mcp_server if p.is_group else None,
                    target_server_id=p.target_server_id,
                )
                if refusal is not None:
                    approval_span.set_attribute("approval.result", "revalidation_failed")
                    refusal.elapsed_ms = p.elapsed_ms()
                    return refusal
            approval_span.set_attribute("approval.result", "not_required")
        return None

    def _gate_cold_start(self, p: "_CallPipeline") -> CallResult | None:
        """Single-flight cold start of the resolved target."""
        if not (p.mcp_server_obj and p.mcp_server_obj.state.value == "cold"):
            return None
        with p.tracer.start_as_current_span("mcp_server.cold_start") as cs_span:
            cs_span.set_attribute("mcp.server.id", p.target_server_id)
            try:
                self._single_flight.do(
                    p.target_server_id,
                    lambda: p.ctx.command_bus.send(StartMcpServerCommand(mcp_server_id=p.target_server_id)),
                )
                cs_span.set_attribute("cold_start.result", "success")
            except Exception as e:  # noqa: BLE001 -- fault-barrier: mcp_server start failure must return error result, not crash batch
                cs_span.set_attribute("cold_start.result", "error")
                cs_span.record_exception(e)
                return p.refuse(f"Failed to start mcp_server: {e}", "McpServerStartError")
        return None

    def _gate_deferred_digest_pin(self, p: "_CallPipeline") -> CallResult | None:
        """Run the pin check the cold start made possible (#601).

        The cold start published McpServerStarted, so the tool catalogue exists
        now. Still missing afterwards means the tool never appeared in the
        catalogue at all, which for a PINNED tool is unverifiable -> fail closed
        under BLOCK, matching how an uncomputable digest is treated inside the
        gate.
        """
        if not p.digest_pin_deferred:
            return None
        late_projection = p.reresolve_projection()
        if late_projection is not None:
            return self._enforce_digest_pin(p, late_projection, p.pin)
        if p.proj_registry.digest_enforcement(p.call.mcp_server) != DigestEnforcement.BLOCK:
            return None
        logger.info(
            "tool_digest_pin_unresolvable",
            mcp_server_id=p.call.mcp_server,
            tool=p.call.tool,
            tenant_id=p.caller_tenant_id,
        )
        return p.refuse(
            f"Tool '{p.call.tool}' is pinned for this tenant but its schema could not be verified",
            "ToolDigestMismatchError",
        )

    def _gate_cancelled_after_cold_start(self, p: "_CallPipeline") -> CallResult | None:
        if not p.cancel_event.is_set():
            return None
        return p.refuse("Cancelled after cold start", "CancellationError")

    def _relay_upstream_task(self, p: "_CallPipeline", result: CallResult) -> CallResult | None:
        """Upstream MCP task handle (ADR-014 P3).

        Two mutually exclusive outcomes, both an EARLY return BEFORE the
        group-health block (a task creation is NOT a healthy-member outcome, so
        report_success must not fire for it):

         - Relay kill-switch ON (the governed task store is wired on the app ctx,
           which happens ONLY when config.relay_tasks_enabled is True): CAPTURE
           the request context into the CallResult and return it as a success.
           This worker performs NO store write -- per ADR-014 D4 the actual
           register + TaskCreated emit runs on the MAIN LOOP at the hangar_call
           seam, before the handle reaches the client.
         - Kill-switch OFF (store absent): byte-identical to the ADR-008
           relay-only stance -- a clean TaskRelayNotSupported rejection, so the
           client never gets an untracked, unusable handle.

        The store's mere presence on ctx is the kill-switch: the factory wires
        governed_task_store ONLY under `HAS_NATIVE_TASKS and relay_tasks_enabled`
        (see fastmcp_server/factory._enable_governed_tasks), and the real
        ApplicationContext field defaults to None. Reading it here needs no
        config plumbing into the worker.
        """
        if not (result.success and isinstance(result.result, dict) and _is_task_result(result.result)):
            return None
        if getattr(p.ctx, "governed_task_store", None) is not None:
            logger.debug(
                "upstream_task_result_captured_for_relay",
                mcp_server=p.call.mcp_server,
                tool=p.call.tool,
                call_id=p.call.call_id,
            )
            return CallResult(
                index=p.call.index,
                call_id=p.call.call_id,
                success=True,
                result=result.result,
                elapsed_ms=result.elapsed_ms,
                relay_capture=RelayCapture(
                    identity=get_identity_context(),
                    pin=get_current_tool_pin(),
                    target_server_id=p.target_server_id,
                    correlation_id=p.call.call_id,
                    upstream=result.result,
                    logical_mcp_server=p.call.mcp_server,
                    tool=p.call.tool,
                ),
            )
        logger.warning(
            "upstream_task_result_rejected",
            mcp_server=p.call.mcp_server,
            tool=p.call.tool,
            call_id=p.call.call_id,
        )
        return CallResult(
            index=p.call.index,
            call_id=p.call.call_id,
            success=False,
            error=(
                "Upstream returned an MCP task handle; Hangar does not yet relay "
                "or govern task results (relay-only, ADR-008). The task is not "
                "tracked, so the handle is unusable."
            ),
            error_type="TaskRelayNotSupported",
            elapsed_ms=result.elapsed_ms,
        )

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

        # Interceptor mutators (request): transform the outgoing arguments payload
        # once, before dispatch (and before any retry). Empty pipeline (default)
        # returns the arguments unchanged, preserving current behavior.
        mutated_arguments = self._mutate("tools/call", "request", call.arguments or {}, call.call_id)

        def do_invoke() -> dict[str, Any]:
            with tracer.start_as_current_span("command.send.InvokeToolCommand") as cmd_span:
                cmd_span.set_attribute("mcp.server.id", dispatch_server_id)
                cmd_span.set_attribute("gen_ai.tool.name", call.tool)
                cmd_span.set_attribute("command.timeout", effective_timeout)
                command = InvokeToolCommand(
                    mcp_server_id=dispatch_server_id,
                    tool_name=call.tool,
                    arguments=mutated_arguments,
                    timeout=effective_timeout,
                    # A granted (and revalidated) approval converts the L7
                    # requireApproval verdict in the aggregate (#921); None
                    # when nothing was granted, and deny still wins inside.
                    l7_approval_id=getattr(_approval_loop_local, "approval_id", None),
                    progress_token=call.progress_token,
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
                retry_span.set_attribute("gen_ai.tool.name", call.tool)
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

                _log_call_failure(call, retry_result.final_error, error_type, elapsed_ms)

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

                _log_call_failure(call, e, error_type, elapsed_ms)

                return CallResult(
                    index=call.index,
                    call_id=call.call_id,
                    success=False,
                    error=str(e),
                    error_type=error_type,
                    elapsed_ms=elapsed_ms,
                )

        # Interceptor mutators (response): transform the returned result payload
        # after a successful invoke, before the size check and building the
        # success CallResult. Empty pipeline (default) returns it unchanged.
        result = self._mutate("tools/call", "response", cast(dict[str, Any], result), call.call_id)

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


#: The order the gates run in. This IS the precedence contract -- which gate
#: answers decides what the caller does next, so reordering two lines here
#: changes behaviour. tests/unit/test_batch_gate_precedence.py pins it by
#: arranging pairs to fail at once and asserting which one wins.
_GATES = (
    BatchExecutor._gate_cancelled_before_execution,
    BatchExecutor._gate_global_timeout,
    BatchExecutor._gate_resolve_target,
    BatchExecutor._gate_tool_access,
    BatchExecutor._gate_withdrawal,
    BatchExecutor._gate_digest_pin,
    BatchExecutor._gate_circuit_breaker,
    BatchExecutor._gate_validators,
    BatchExecutor._gate_approval,
    BatchExecutor._gate_cold_start,
    BatchExecutor._gate_deferred_digest_pin,
    BatchExecutor._gate_cancelled_after_cold_start,
)
