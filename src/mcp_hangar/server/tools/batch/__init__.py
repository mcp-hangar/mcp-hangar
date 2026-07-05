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
import uuid

from mcp.server.fastmcp import Context, FastMCP

from ....application.services.interceptor_registry import build_validator_pipeline
from ....context import get_identity_context, identity_context_var
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

        # Build call specs with retry configuration
        call_specs = [
            CallSpec(
                index=i,
                call_id=str(uuid.uuid4()),
                mcp_server=call["mcp_server"],
                tool=call["tool"],
                arguments=call["arguments"],
                timeout=call.get("timeout"),
                max_retries=max_attempts,  # Internal field uses max_retries
            )
            for i, call in enumerate(calls)
        ]

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

        # Execute batch -- the executor uses ThreadPoolExecutor internally
        # for parallel call execution.
        try:
            result = _executor.execute(
                batch_id=batch_id,
                calls=call_specs,
                max_concurrency=max_concurrency,
                global_timeout=timeout,
                fail_fast=fail_fast,
            )
        finally:
            if _identity_token is not None:
                identity_context_var.reset(_identity_token)

        root_span.set_attribute("batch.result", "success" if result.success else "failure")
        root_span.set_attribute("batch.succeeded", result.succeeded)
        root_span.set_attribute("batch.failed", result.failed)

        # Convert to dict response
        return {
            "batch_id": result.batch_id,
            "success": result.success,
            "total": result.total,
            "succeeded": result.succeeded,
            "failed": result.failed,
            "elapsed_ms": round(result.elapsed_ms, 2),
            "results": [format_result_dict(r) for r in result.results],
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
