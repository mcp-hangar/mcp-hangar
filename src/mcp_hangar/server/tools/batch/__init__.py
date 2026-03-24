"""Batch invocation tool for MCP Hangar.

Executes multiple tool invocations in parallel with configurable concurrency,
timeout handling, and fail-fast behavior.

Features:
- Parallel execution with ThreadPoolExecutor
- Two-level semaphore concurrency control (global + per-provider)
- Single-flight pattern for cold starts (one provider starts once, not N times)
- Cooperative cancellation via threading.Event
- Eager validation before execution
- Partial success handling (default: continue on error)
- Response truncation for oversized payloads
- Circuit breaker integration

Example:
    hangar_call(calls=[
        {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
        {"provider": "math", "tool": "multiply", "arguments": {"a": 3, "b": 4}},
    ])
"""

from typing import Any
import uuid

from mcp.server.fastmcp import FastMCP

from ....logging_config import get_logger
from ....metrics import BATCH_CALLS_TOTAL, BATCH_VALIDATION_FAILURES_TOTAL
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


def hangar_call(
    calls: list[dict[str, Any]],
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    timeout: float = DEFAULT_TIMEOUT,
    fail_fast: bool = False,
    max_attempts: int = 1,
) -> dict[str, Any]:
    """Invoke tools on MCP providers (single or batch).

    CHOOSE THIS when: you want to execute tool(s) on provider(s). This is the main entry point.
    CHOOSE hangar_tools when: you need to discover available tools before calling.
    CHOOSE hangar_start when: you only want to pre-warm without invoking.

    Side effects: May start cold providers. Executes calls in parallel.

    Concurrency model:
        Two levels of concurrency control apply simultaneously:
        1. Per-batch: max_concurrency limits threads for THIS invocation.
        2. System-wide: global and per-provider semaphores (configured via
           config.yaml ``execution.max_concurrency`` and per-provider
           ``max_concurrency``) provide cross-batch backpressure.

        All calls are submitted to the thread pool at once. Semaphores gate
        execution -- a call starts as soon as a slot frees up, without waiting
        for the entire batch wave to complete.

    Args:
        calls: list[{provider, tool, arguments, timeout?}] - Invocations to execute
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
        hangar_call(calls=[{"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}}])
        # {"batch_id": "abc-123", "success": true, "total": 1, "succeeded": 1, "failed": 0,
        #  "elapsed_ms": 45.2, "results": [{"index": 0, "call_id": "def-456",
        #  "success": true, "result": 3, "error": null, "elapsed_ms": 42.1}]}

        # Validation error - unknown provider
        hangar_call(calls=[{"provider": "unknown", "tool": "x", "arguments": {}}])
        # {"batch_id": "abc-123", "success": false, "error": "Validation failed",
        #  "validation_errors": [{"index": 0, "field": "provider", "message": "..."}]}

        # Partial failure - some succeed, some fail
        hangar_call(calls=[
            {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
            {"provider": "math", "tool": "divide", "arguments": {"a": 1, "b": 0}}
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
    validation_errors = validate_batch(calls, max_concurrency, timeout)
    if validation_errors:
        BATCH_VALIDATION_FAILURES_TOTAL.inc()
        BATCH_CALLS_TOTAL.inc(result="validation_error")
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
            provider=call["provider"],
            tool=call["tool"],
            arguments=call["arguments"],
            timeout=call.get("timeout"),
            max_retries=max_attempts,  # Internal field uses max_retries
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
