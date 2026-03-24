"""Batch validation logic.

Provides eager validation of batch invocations before execution.
"""

from typing import Any

from ...context import get_context
from ...state import GROUPS
from .models import MAX_CALLS_PER_BATCH, MAX_CONCURRENCY_LIMIT, MAX_TIMEOUT, ValidationError


def validate_batch(
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

        # Provider exists (check both providers and groups via context)
        ctx = get_context()
        provider_obj = ctx.get_provider(provider)
        if not provider_obj:
            provider_obj = GROUPS.get(provider)
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
            tool_schema = provider_obj.tools.get(tool)
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
