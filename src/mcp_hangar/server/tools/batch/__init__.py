"""Batch invocation tool for MCP Hangar.

Executes multiple tool invocations in parallel with configurable concurrency,
timeout handling, and fail-fast behavior.

Features:
- Parallel execution with ThreadPoolExecutor
- Two-level semaphore concurrency control (global + per-mcp_server)
- Single-flight pattern for cold starts (one mcp_server starts once, not N times)
- Cooperative cancellation via threading.Event
- Eager validation before execution
- Partial success handling (default: continue on error)
- Response truncation for oversized payloads
- Circuit breaker integration

Example:
    hangar_call(calls=[
        {"mcp_server": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
        {"mcp_server": "math", "tool": "multiply", "arguments": {"a": 3, "b": 4}},
    ])
"""

from typing import Any
import time
import uuid

from mcp_hangar._sdk_compat import Context, FastMCP

from ....application.services.interceptor_registry import build_validator_pipeline
from ....application.tasks.tool_pin_context import reset_current_tool_pin, set_current_tool_pin
from ....context import get_identity_context, identity_context_var
from ....domain.services.task_ownership import TaskOwner
from ....logging_config import get_logger
from ....metrics import BATCH_CALLS_TOTAL, BATCH_VALIDATION_FAILURES_TOTAL
from ....observability.tracing import get_tracer
from .concurrency import (
    ConcurrencyManager,
    DEFAULT_GLOBAL_CONCURRENCY,
    DEFAULT_PROVIDER_CONCURRENCY,
    get_concurrency_manager,
    init_concurrency_manager,
    reset_concurrency_manager,
)
from ...context import get_context
from .executor import BatchExecutor, format_result_dict
from .models import (
    BatchResult,
    CallResult,
    CallSpec,
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    MAX_CALLS_PER_BATCH,
    MAX_CONCURRENCY_LIMIT,
    MAX_RESPONSE_SIZE_BYTES,
    MAX_TIMEOUT,
    MAX_TOTAL_RESPONSE_SIZE_BYTES,
    RetryMetadata,
    ValidationError,
)
from .validator import validate_batch

logger = get_logger(__name__)

# Global executor instance
_executor = BatchExecutor()


def configure_interceptors(validator_specs: list[dict[str, Any]] | None = None) -> None:
    """Rebuild the global executor with an opt-in interceptor configuration.

    Called ONCE at startup after config load. Off by default: an empty or
    absent ``validator_specs`` yields an empty ValidatorPipeline, so no
    validators run and behavior is unchanged.

    Args:
        validator_specs: The parsed ``interceptors.validators`` list (each item
            a dict with a ``type`` key + per-type params), or ``None`` to
            register no validators.

    Raises:
        ValueError: If a spec names an unknown validator type (see
            :func:`build_validator_pipeline`).
    """
    global _executor
    _executor = BatchExecutor(validator_pipeline=build_validator_pipeline(validator_specs))
    logger.info(
        "interceptors_configured",
        validator_count=len(validator_specs) if validator_specs else 0,
    )


def _authorize_calls(
    calls: list[dict[str, Any]],
    call_ids: list[str],
    ctx: Context | None,
    batch_id: str,
) -> dict[int, CallResult]:
    """Enforce ``tool:invoke`` authorization for each call, fail-closed.

    Mirrors the REST guard ``_check_permission`` (server/api/mcp_servers.py) so
    the ``hangar_call`` tool-invoke path enforces the same RBAC as the REST API
    (previously RBAC #386 covered only REST, so any caller could invoke tools):

    - authz middleware NOT configured (stdio / no-auth / local) -> allow all
      (returns ``{}``; backward compatible, does not break local use).
    - auth IS configured but the principal is missing/anonymous -> deny all.
    - else authorize each call's tool (``action="invoke"``,
      ``resource_type="tool"``, ``resource_id=<tool>``); a denial -- or any
      authorizer error -- yields a denied ``CallResult`` for that call only.

    Returns a mapping of original call index -> denied ``CallResult``. Indexes
    absent from the mapping are authorized and proceed to execution.

    Fully fault-barriered: a missing app/request context (stdio) leaves behavior
    unchanged (allow), because no authz middleware is resolvable.
    """
    # Resolve the authz middleware. A missing app context (stdio/local) or an
    # unconfigured middleware means auth is off -> allow (backward compatible).
    try:
        auth_components = getattr(get_context(), "auth_components", None)
    except Exception:  # noqa: BLE001 -- no app context (stdio/local) -> auth off, allow
        auth_components = None
    authz = getattr(auth_components, "authz_middleware", None)
    # Auth off -> allow (backward compatible). "Off" means either no authz
    # middleware at all (stdio/local) OR an auth_components that is present but
    # explicitly disabled (NullAuthComponents ships a real AuthorizationMiddleware
    # with enabled=False; without the enabled check every invoke on an auth-off
    # HTTP server is denied as anonymous, so --unsafe-no-auth cannot invoke tools).
    if authz is None or not getattr(auth_components, "enabled", False):
        return {}

    # Auth IS configured: resolve the authenticated principal bridged onto the
    # inbound request by the auth middleware (request.state.auth.principal).
    try:
        _auth_state = getattr(getattr(getattr(ctx, "request_context", None), "request", None), "state", None)
        principal = getattr(getattr(_auth_state, "auth", None), "principal", None)
    except Exception:  # noqa: BLE001 -- fault barrier: identity lookup must not crash the call path
        principal = None

    denied: dict[int, CallResult] = {}

    # Missing/anonymous principal under configured auth -> deny the whole batch.
    if principal is None or principal.is_anonymous():
        logger.warning(
            "hangar_call_authorization_denied",
            batch_id=batch_id,
            reason="missing_credentials",
            call_count=len(calls),
        )
        for i, _call in enumerate(calls):
            denied[i] = CallResult(
                index=i,
                call_id=call_ids[i],
                success=False,
                error="Authentication required to invoke tools",
                error_type="AuthorizationDenied",
                elapsed_ms=0.0,
            )
        return denied

    # Per-call authorization: a principal lacking tool:invoke (e.g. viewer) is
    # denied fail-closed; authorized principals (e.g. developer) proceed.
    for i, call in enumerate(calls):
        tool = call.get("tool", "")
        try:
            authz.authorize(
                principal=principal,
                action="invoke",
                resource_type="tool",
                resource_id=tool,
            )
        except Exception as exc:  # noqa: BLE001 -- fail-closed: any authz denial/error rejects this call
            logger.warning(
                "hangar_call_authorization_denied",
                batch_id=batch_id,
                tool=tool,
                reason=type(exc).__name__,
            )
            denied[i] = CallResult(
                index=i,
                call_id=call_ids[i],
                success=False,
                error=f"Not authorized to invoke tool '{tool}': tool:invoke permission required",
                error_type="AuthorizationDenied",
                elapsed_ms=0.0,
            )
    return denied


def _govern_relayed_tasks(executed: list[CallResult]) -> None:
    """P3.3 relay seam: govern captured upstream task handles ON THE MAIN LOOP.

    ADR-014 D4 binds governance on the request path, never in a worker thread.
    Each batch worker that saw an upstream task handle (kill-switch on) attached a
    :class:`RelayCapture` to its (success) CallResult but performed NO store write.
    Here, back on the main loop and BEFORE the client-visible batch response is
    assembled, we run the atomic ``store.relay_and_govern`` (register +
    ``TaskCreated`` emit) for each such result, rewriting ``executed`` in place.

    Outcomes per captured result:
      - store absent (kill-switch off / no app ctx) -> safety: rewrite to the
        TaskRelayNotSupported rejection (never hand back an ungoverned handle).
      - mint/register/emit fails -> rewrite to a DISTINCT
        ``TaskRelayRegistrationFailed`` failure; ``relay_and_govern``'s atomic
        rollback guarantees zero governed state survives.
      - success -> a pre-built success CallResult carrying the raw, now-governed
        upstream handle.

    The captured identity (and digest pin) are re-bound into their contextvars for
    the duration so ``relay_and_govern``'s owner cross-check and digest pin see the
    same request context the worker authorized -- not a live/foreign contextvar.
    """
    try:
        store = getattr(get_context(), "governed_task_store", None)
    except Exception:  # noqa: BLE001 -- no app context (stdio/local): treat as kill-switch off
        store = None

    for i, r in enumerate(executed):
        capture = r.relay_capture
        if capture is None:
            continue

        if store is None:
            # Safety net: a capture with no store to govern it must never reach the
            # client as a live handle. Fall back to the relay-only rejection.
            executed[i] = CallResult(
                index=r.index,
                call_id=r.call_id,
                success=False,
                error=(
                    "Upstream returned an MCP task handle; Hangar does not yet relay "
                    "or govern task results (relay-only, ADR-008). The task is not "
                    "tracked, so the handle is unusable."
                ),
                error_type="TaskRelayNotSupported",
                elapsed_ms=r.elapsed_ms,
            )
            continue

        seam_start = time.perf_counter()
        # Re-bind the CAPTURED request context (identity + digest pin) for the
        # duration of the governed relay, then always restore it.
        _id_token = identity_context_var.set(capture.identity)
        _pin_token = set_current_tool_pin(capture.pin) if capture.pin is not None else None
        try:
            try:
                # capture.upstream is the raw upstream ``CreateTaskResult``
                # (``{"task": {...}}``) -- byte-identical to what the client will
                # receive. ``mint_from_upstream`` mints from the FLAT task object,
                # so unwrap the ``task`` member here (a non-dict / missing member
                # is malformed -> fail closed via mint's ValueError).
                _task_obj = capture.upstream.get("task") if isinstance(capture.upstream, dict) else None
                task = store.mint_from_upstream(_task_obj if isinstance(_task_obj, dict) else {})
            except ValueError as exc:
                # Fail-closed extraction: a malformed/idless upstream task handle.
                # TODO(P3.4): increment a relay-registration-failure metric counter.
                logger.warning(
                    "task_relay_mint_failed",
                    call_id=r.call_id,
                    mcp_server=capture.logical_mcp_server,
                    tool=capture.tool,
                    error=str(exc),
                )
                executed[i] = CallResult(
                    index=r.index,
                    call_id=r.call_id,
                    success=False,
                    error=f"Failed to register relayed task: {exc}",
                    error_type="TaskRelayRegistrationFailed",
                    elapsed_ms=r.elapsed_ms + (time.perf_counter() - seam_start) * 1000,
                )
                continue

            # Pre-build the final success result now (mint done), so once the
            # atomic register+publish below succeeds nothing fallible remains --
            # elapsed honestly includes the mint/register/emit cost.
            success_result = CallResult(
                index=r.index,
                call_id=r.call_id,
                success=True,
                result=capture.upstream,
                elapsed_ms=r.elapsed_ms + (time.perf_counter() - seam_start) * 1000,
            )

            if capture.identity is not None and capture.identity.caller is not None:
                _caller = capture.identity.caller
                expected_owner = TaskOwner(
                    tenant_id=_caller.tenant_id,
                    principal_id=_caller.user_id or _caller.agent_id,
                )
            else:
                expected_owner = TaskOwner(tenant_id=None, principal_id=None)

            try:
                store.relay_and_govern(
                    target_server_id=capture.target_server_id,
                    task=task,
                    expected_owner=expected_owner,
                    correlation_id=capture.correlation_id,
                    mcp_server_id=capture.logical_mcp_server,
                    tool_name=capture.tool,
                )
            except Exception as exc:  # noqa: BLE001 -- any register/emit failure -> distinct fail-closed result
                # relay_and_govern's atomic rollback leaves ZERO governed state.
                # TODO(P3.4): increment a relay-registration-failure metric counter.
                logger.warning(
                    "task_relay_registration_failed",
                    call_id=r.call_id,
                    mcp_server=capture.logical_mcp_server,
                    tool=capture.tool,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                executed[i] = CallResult(
                    index=r.index,
                    call_id=r.call_id,
                    success=False,
                    error=f"Failed to register relayed task: {exc}",
                    error_type="TaskRelayRegistrationFailed",
                    elapsed_ms=r.elapsed_ms + (time.perf_counter() - seam_start) * 1000,
                )
                continue

            # Governed: hand the client the raw, now-tracked upstream handle.
            executed[i] = success_result
        finally:
            if _pin_token is not None:
                reset_current_tool_pin(_pin_token)
            identity_context_var.reset(_id_token)


def hangar_call(
    calls: list[dict[str, Any]],
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    timeout: float = DEFAULT_TIMEOUT,
    fail_fast: bool = False,
    max_attempts: int = 1,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Invoke tools on MCP mcp_servers (single or batch).

    CHOOSE THIS when: you want to execute tool(s) on mcp_server(s). This is the main entry point.
    CHOOSE hangar_tools when: you need to discover available tools before calling.
    CHOOSE hangar_start when: you only want to pre-warm without invoking.

    Side effects: May start cold mcp_servers. Executes calls in parallel.

    Concurrency model:
        Two levels of concurrency control apply simultaneously:
        1. Per-batch: max_concurrency limits threads for THIS invocation.
        2. System-wide: global and per-mcp_server semaphores (configured via
           config.yaml ``execution.max_concurrency`` and per-mcp_server
           ``max_concurrency``) provide cross-batch backpressure.

        All calls are submitted to the thread pool at once. Semaphores gate
        execution -- a call starts as soon as a slot frees up, without waiting
        for the entire batch wave to complete.

    Args:
        calls: list[{mcp_server, tool, arguments, timeout?}] - Invocations to execute
        max_concurrency: int - Parallel workers for this batch (default: 10, range: 1-50)
        timeout: float - Batch timeout in seconds (default: 60, range: 1-300)
        fail_fast: bool - Stop batch on first error (default: false)
        max_attempts: int - Total attempts per call including retries (default: 1, range: 1-10)

    Returns:
        Success: {
            batch_id: str,
            success: true,
            total: int,
            succeeded: int,
            failed: int,
            elapsed_ms: float,
            results: [{
                index: int,
                call_id: str,
                success: true,
                result: any,
                error: null,
                error_type: null,
                elapsed_ms: float
            }]
        }
        Partial failure: {
            batch_id: str,
            success: false,
            total: int,
            succeeded: int,
            failed: int,
            elapsed_ms: float,
            results: [{
                index: int,
                call_id: str,
                success: bool,
                result: any | null,
                error: str | null,
                error_type: str | null,
                elapsed_ms: float,
                retry_metadata?: {attempts: int, retries: list}
            }]
        }
        Validation error: {
            batch_id: str,
            success: false,
            error: "Validation failed",
            validation_errors: [{index: int, field: str, message: str}]
        }
        Truncated result: Individual result contains additional fields:
            {truncated: true, truncated_reason: str, original_size_bytes: int, continuation_id: str}
            Retrieve full data with hangar_fetch_continuation(continuation_id).

    Example:
        # Single call - success
        hangar_call(calls=[{"mcp_server": "math", "tool": "add", "arguments": {"a": 1, "b": 2}}])
        # {"batch_id": "abc-123", "success": true, "total": 1, "succeeded": 1, "failed": 0,
        #  "elapsed_ms": 45.2, "results": [{"index": 0, "call_id": "def-456",
        #  "success": true, "result": 3, "error": null, "elapsed_ms": 42.1}]}

        # Validation error - unknown mcp_server
        hangar_call(calls=[{"mcp_server": "unknown", "tool": "x", "arguments": {}}])
        # {"batch_id": "abc-123", "success": false, "error": "Validation failed",
        #  "validation_errors": [{"index": 0, "field": "mcp_server", "message": "..."}]}

        # Partial failure - some succeed, some fail
        hangar_call(calls=[
            {"mcp_server": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
            {"mcp_server": "math", "tool": "divide", "arguments": {"a": 1, "b": 0}}
        ])
        # {"batch_id": "...", "success": false, "total": 2, "succeeded": 1, "failed": 1,
        #  "results": [
        #    {"index": 0, "success": true, "result": 3, ...},
        #    {"index": 1, "success": false, "error": "division by zero", "error_type": "ValueError"}
        #  ]}

        # With retry - shows retry_metadata on failure
        hangar_call(calls=[...], max_attempts=3)
        # On failure: {"results": [{"retry_metadata": {"attempts": 3, "retries": [...]}, ...}]}
    """
    batch_id = str(uuid.uuid4())

    # Clamp max_attempts to valid range
    max_attempts = max(1, min(max_attempts, 10))

    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("hangar_call") as root_span:
        root_span.set_attribute("batch.id", batch_id)
        root_span.set_attribute("batch.call_count", len(calls))
        root_span.set_attribute("batch.max_concurrency", max_concurrency)
        root_span.set_attribute("batch.timeout", timeout)
        root_span.set_attribute("batch.fail_fast", fail_fast)
        root_span.set_attribute("batch.max_attempts", max_attempts)

        logger.info(
            "hangar_call_requested",
            batch_id=batch_id,
            call_count=len(calls),
            max_concurrency=max_concurrency,
            timeout=timeout,
            fail_fast=fail_fast,
            max_attempts=max_attempts,
        )

        # Handle empty batch
        if not calls:
            logger.debug("hangar_call_empty", batch_id=batch_id)
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
        with tracer.start_as_current_span("hangar_call.validate") as val_span:
            validation_errors = validate_batch(calls, max_concurrency, timeout)
            val_span.set_attribute("validation.error_count", len(validation_errors))
        if validation_errors:
            BATCH_VALIDATION_FAILURES_TOTAL.inc()
            BATCH_CALLS_TOTAL.inc(result="validation_error")
            root_span.set_attribute("batch.result", "validation_error")
            logger.warning(
                "hangar_call_validation_failed",
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

        # Stable call ids, one per call, shared by the authorization gate and the
        # executed call specs so a denied and an executed call carry the same id.
        call_ids = [str(uuid.uuid4()) for _ in calls]

        # Authorization gate (fail-closed): enforce tool:invoke per call BEFORE
        # execution, mirroring the REST guard. Denied calls never reach the
        # executor; authorized calls proceed. No-auth/stdio -> allow all.
        with tracer.start_as_current_span("hangar_call.authorize") as authz_span:
            denied_by_index = _authorize_calls(calls, call_ids, ctx, batch_id)
            authz_span.set_attribute("authz.denied_count", len(denied_by_index))

        # Build call specs for the AUTHORIZED calls only. Give the executor a
        # contiguous index space (it sizes its result list by len(specs)); the
        # original call index is remembered in exec_to_orig for remapping back.
        call_specs: list[CallSpec] = []
        exec_to_orig: list[int] = []
        for i, call in enumerate(calls):
            if i in denied_by_index:
                continue
            call_specs.append(
                CallSpec(
                    index=len(call_specs),
                    call_id=call_ids[i],
                    mcp_server=call["mcp_server"],
                    tool=call["tool"],
                    arguments=call["arguments"],
                    timeout=call.get("timeout"),
                    max_retries=max_attempts,  # Internal field uses max_retries
                )
            )
            exec_to_orig.append(i)

        # Bridge the authenticated caller identity into the tool-call path over
        # streamable-HTTP. FastMCP's streamable-HTTP transport runs tool calls in
        # a per-session task decoupled from the ASGI auth wrapper coroutine that
        # sets identity_context_var, so that contextvar is None here for an
        # authenticated HTTP caller. The FastMCP-injected request context, however,
        # IS reachable, and the auth middleware stored the principal on the request
        # (request.state.auth). Read it and set identity_context_var so the executor
        # -- which snapshots contextvars into its worker threads via copy_context --
        # sees the real tenant for per-tenant enforcement (canary routing #283,
        # per-tenant tool withdrawal). Fully fault-barriered: stdio / no-request /
        # unauthenticated paths leave identity as None (existing fallback unchanged).
        # We only bridge when identity is not already bound (never override the ASGI
        # wrapper when it did propagate). The token is reset in finally to avoid
        # leaking identity across calls in a reused per-session task.
        _identity_token = None
        if get_identity_context() is None:
            try:
                _auth = getattr(getattr(getattr(ctx, "request_context", None), "request", None), "state", None)
                _principal = getattr(getattr(_auth, "auth", None), "principal", None)
                if _principal is not None:
                    from ....fastmcp_server.asgi import _principal_to_identity_context

                    _identity_token = identity_context_var.set(_principal_to_identity_context(_principal))
            except Exception:  # noqa: BLE001 -- identity bridging must never break the call path
                _identity_token = None

        # Execute the authorized calls -- the executor uses ThreadPoolExecutor
        # internally for parallel call execution. If every call was denied by the
        # authorization gate, skip the executor entirely.
        executed: list[CallResult] = []
        exec_elapsed_ms = 0.0
        if call_specs:
            try:
                result = _executor.execute(
                    batch_id=batch_id,
                    calls=call_specs,
                    max_concurrency=max_concurrency,
                    global_timeout=timeout,
                    fail_fast=fail_fast,
                    # Thread the real FastMCP request context so the executor reads
                    # inbound trace context + protocol negotiation from the actual
                    # params._meta over streamable-HTTP (the ApplicationContext has
                    # none). None on stdio -> defaults, unchanged. Identity bridging
                    # above (#387) is untouched.
                    request_ctx=ctx,
                )
            finally:
                if _identity_token is not None:
                    identity_context_var.reset(_identity_token)
            executed = result.results
            exec_elapsed_ms = result.elapsed_ms
            # P3.3 relay seam (ADR-014 D4): govern any upstream task handles a
            # worker captured -- register + emit TaskCreated ON THE MAIN LOOP,
            # BEFORE the batch response is assembled/returned to the client.
            _govern_relayed_tasks(executed)
        elif _identity_token is not None:
            identity_context_var.reset(_identity_token)

        # Merge authorization denials with executed results into original order.
        merged: list[CallResult | None] = [None] * len(calls)
        for orig_index, denied_result in denied_by_index.items():
            merged[orig_index] = denied_result
        for r in executed:
            orig_index = exec_to_orig[r.index]
            r.index = orig_index  # remap the executor's contiguous index back
            merged[orig_index] = r

        final_results = [r for r in merged if r is not None]
        succeeded = sum(1 for r in final_results if r.success)
        failed = len(final_results) - succeeded
        success = failed == 0

        root_span.set_attribute("batch.result", "success" if success else "failure")
        root_span.set_attribute("batch.succeeded", succeeded)
        root_span.set_attribute("batch.failed", failed)
        root_span.set_attribute("batch.authz_denied", len(denied_by_index))

        # Convert to dict response
        return {
            "batch_id": batch_id,
            "success": success,
            "total": len(final_results),
            "succeeded": succeeded,
            "failed": failed,
            "elapsed_ms": round(exec_elapsed_ms, 2),
            "results": [format_result_dict(r) for r in final_results],
        }


def register_batch_tools(mcp: FastMCP) -> None:
    """Register invocation tools with the MCP server.

    Registers hangar_call as the unified invocation tool.

    Args:
        mcp: FastMCP server instance.
    """
    mcp.tool()(hangar_call)
    logger.info("hangar_call_tool_registered")


# Backward compatibility - expose internal function with underscore prefix
_validate_batch = validate_batch
_format_result_dict = format_result_dict

__all__ = [
    # Main API
    "hangar_call",
    "register_batch_tools",
    "configure_interceptors",
    # Models
    "BatchResult",
    "CallResult",
    "CallSpec",
    "RetryMetadata",
    "ValidationError",
    # Constants
    "DEFAULT_MAX_CONCURRENCY",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT",
    "MAX_CALLS_PER_BATCH",
    "MAX_CONCURRENCY_LIMIT",
    "MAX_RESPONSE_SIZE_BYTES",
    "MAX_TIMEOUT",
    "MAX_TOTAL_RESPONSE_SIZE_BYTES",
    # Concurrency
    "ConcurrencyManager",
    "DEFAULT_GLOBAL_CONCURRENCY",
    "DEFAULT_PROVIDER_CONCURRENCY",
    "get_concurrency_manager",
    "init_concurrency_manager",
    "reset_concurrency_manager",
    # Executor
    "BatchExecutor",
    # Internal (backward compat)
    "_validate_batch",
    "_format_result_dict",
]
