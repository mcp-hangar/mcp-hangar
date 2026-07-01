"""IValidator contract for the interceptor framework (Validator model, PR #2624).

Companion to :mod:`mcp_hangar.domain.contracts.mutator`. Validators decide whether
an MCP payload may proceed (allow / deny); Mutators transform it. Per PR #2624 a
validator is **fail-closed by default**: if it raises, the pipeline denies unless the
validator explicitly opts into ``fail_open``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

# Shared with the mutator contract's ordering range; redefined here to keep this
# module dependency-free.
_INT32_MIN = -2_147_483_648
_INT32_MAX = 2_147_483_647


@dataclass(frozen=True)
class ValidationContext:
    """Input to a validator invocation.

    Attributes:
        method: MCP method name (e.g. "tools/call").
        direction: Whether this is a request or response payload.
        payload: The JSON-serializable payload to validate.
        correlation_id: Request correlation ID for audit trail linkage.
    """

    method: str
    direction: Literal["request", "response"]
    payload: dict[str, Any]
    correlation_id: str

    def __post_init__(self) -> None:
        if not self.method:
            raise ValueError("method cannot be empty")
        if not self.correlation_id:
            raise ValueError("correlation_id cannot be empty")


@dataclass(frozen=True)
class ValidationResult:
    """Output of a validator invocation.

    Attributes:
        allowed: True if the payload may proceed.
        reason: Human-readable justification when denied (or for audit).
        audit_only: Shadow mode; a denial with ``audit_only`` is recorded but NOT
            enforced (the pipeline still allows the payload through).
    """

    allowed: bool
    reason: str | None = None
    audit_only: bool = False

    @classmethod
    def allow(cls) -> ValidationResult:
        return cls(allowed=True)

    @classmethod
    def deny(cls, reason: str, *, audit_only: bool = False) -> ValidationResult:
        return cls(allowed=False, reason=reason, audit_only=audit_only)


@runtime_checkable
class IValidator(Protocol):
    """Contract for components that gate MCP payloads (allow / deny).

    Validators are ordered by ``priority_hint`` ascending (lower runs first) and
    only run for methods in ``applies_to``.
    """

    @property
    def priority_hint(self) -> int:
        """Sequential ordering. Lower runs first. Range: int32."""
        ...

    @property
    def applies_to(self) -> frozenset[str]:
        """MCP method names this validator gates."""
        ...

    @property
    def fail_open(self) -> bool:
        """Whether a raised exception is treated as allow (skip) rather than deny.

        MUST default to False (fail-closed) per PR #2624: a failing interceptor
        rejects the request unless explicitly configured open.
        """
        ...

    def validate(self, context: ValidationContext) -> ValidationResult:
        """Return whether the payload may proceed."""
        ...
