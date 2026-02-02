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
    max_retries: int = 1,
) -> dict[str, Any]:
    """Invoke tools on MCP providers.

    The single entry point for all tool invocations. Supports single calls,
    batch execution, retries, and timeouts. Cold providers auto-start.

    Args:
        calls: List of invocations. Each call requires:
            - provider: Provider ID or Group ID (routes to healthy member)
            - tool: Tool name (use hangar_tools to discover available tools)
            - arguments: Tool arguments as dict
            - timeout: (optional) Per-call timeout in seconds
        max_concurrency: Parallel workers, 1-20 (default: 10)
        timeout: Batch timeout in seconds, 1-300 (default: 60)
        fail_fast: Stop on first error (default: false, continues all calls)
        max_retries: Retry attempts per call, 1-10 (default: 1 = no retry)

    Returns:
        - batch_id: UUID for tracing
        - success: true if ALL calls succeeded
        - total/succeeded/failed: Call counts
        - elapsed_ms: Total time
        - results: Per-call results with index, success, result/error
            If a result is truncated due to size, it will have:
            {"truncated": true, "continuation_id": "cont_..."}
            Use hangar_fetch_continuation with that ID to get the full response.

    Example:
        # Single call
        hangar_call(calls=[{"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}}])
        # Returns:
        # {
        #   "batch_id": "...",
        #   "success": true,
        #   "total": 1, "succeeded": 1, "failed": 0,
        #   "elapsed_ms": 45.2,
        #   "results": [{"index": 0, "success": true, "result": 3, "elapsed_ms": 42.1}]
        # }

        # Call via group (auto-routes to healthy member)
        hangar_call(calls=[{"provider": "llm-group", "tool": "complete", "arguments": {"prompt": "Hi"}}])

        # Batch with retry
        hangar_call(
            calls=[
                {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
                {"provider": "math", "tool": "divide", "arguments": {"a": 10, "b": 0}}
            ],
            max_retries=3
        )
        # Returns: {"success": false, "succeeded": 1, "failed": 1, "results": [...]}
    """
    batch_id = str(uuid.uuid4())

    # Clamp max_retries to valid range
    max_retries = max(1, min(max_retries, 10))

    logger.info(
        "hangar_call_requested",
        batch_id=batch_id,
        call_count=len(calls),
        max_concurrency=max_concurrency,
        timeout=timeout,
        fail_fast=fail_fast,
        max_retries=max_retries,
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
            max_retries=max_retries,
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
    # Executor
    "BatchExecutor",
    # Internal (backward compat)
    "_validate_batch",
    "_format_result_dict",
]
