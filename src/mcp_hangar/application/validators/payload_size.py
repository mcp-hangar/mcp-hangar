"""Reference IValidator: cap the size of inbound request payloads.

The request-side counterpart to ``ResponseTruncator`` (which caps response size).
Denies ``tools/call`` requests whose JSON-encoded payload exceeds a byte limit,
guarding upstream servers from oversized arguments. Fail-closed (``fail_open`` is
False), so a serialization error also denies.
"""

from __future__ import annotations

import json

from mcp_hangar.domain.contracts.validator import ValidationContext, ValidationResult

_DEFAULT_MAX_BYTES = 1_000_000
_DEFAULT_PRIORITY = 1000


class PayloadSizeValidator:
    """Deny requests whose JSON payload exceeds ``max_bytes``."""

    def __init__(self, max_bytes: int = _DEFAULT_MAX_BYTES, priority_hint: int = _DEFAULT_PRIORITY) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._max_bytes = max_bytes
        self._priority_hint = priority_hint

    @property
    def priority_hint(self) -> int:
        return self._priority_hint

    @property
    def applies_to(self) -> frozenset[str]:
        return frozenset({"tools/call"})

    @property
    def fail_open(self) -> bool:
        return False

    def validate(self, context: ValidationContext) -> ValidationResult:
        size = len(json.dumps(context.payload).encode("utf-8"))
        if size > self._max_bytes:
            return ValidationResult.deny(f"payload {size} bytes exceeds cap {self._max_bytes}")
        return ValidationResult.allow()
