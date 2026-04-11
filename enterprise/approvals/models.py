"""Approval gate domain models.

ApprovalRequest is the aggregate tracking a single approval lifecycle.
ApprovalResult is the value object returned to the tool wrapper.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ApprovalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


@dataclass
class ApprovalRequest:
    """Aggregate tracking a single tool approval lifecycle.

    Starts in PENDING state, transitions to exactly one terminal state.
    """

    approval_id: str
    provider_id: str
    tool_name: str
    arguments: dict[str, Any]
    arguments_hash: str
    requested_at: datetime
    expires_at: datetime
    state: ApprovalState
    channel: str
    decided_by: str | None = None
    decided_at: datetime | None = None
    reason: str | None = None
    correlation_id: str = ""

    def is_terminal(self) -> bool:
        return self.state in (
            ApprovalState.APPROVED,
            ApprovalState.DENIED,
            ApprovalState.EXPIRED,
        )

    def approve(self, decided_by: str) -> None:
        """Transition PENDING -> APPROVED. Raises if already terminal."""
        if self.is_terminal():
            raise ValueError(f"Cannot approve request in state {self.state}")
        self.state = ApprovalState.APPROVED
        self.decided_by = decided_by
        self.decided_at = datetime.now(timezone.utc)

    def deny(self, decided_by: str, reason: str | None = None) -> None:
        """Transition PENDING -> DENIED."""
        if self.is_terminal():
            raise ValueError(f"Cannot deny request in state {self.state}")
        self.state = ApprovalState.DENIED
        self.decided_by = decided_by
        self.decided_at = datetime.now(timezone.utc)
        self.reason = reason

    def expire(self) -> None:
        """Transition PENDING -> EXPIRED. Idempotent on already-terminal."""
        if self.state != ApprovalState.PENDING:
            return
        self.state = ApprovalState.EXPIRED
        self.decided_at = datetime.now(timezone.utc)


@dataclass(frozen=True)
class ApprovalResult:
    """Immutable result returned to the mcp_tool_wrapper check_approval hook."""

    approved: bool
    approval_id: str | None
    error_code: str | None = None
    reason: str | None = None

    @classmethod
    def not_required(cls) -> "ApprovalResult":
        return cls(approved=True, approval_id=None)

    @classmethod
    def granted(cls, approval_id: str) -> "ApprovalResult":
        return cls(approved=True, approval_id=approval_id)

    @classmethod
    def denied(cls, approval_id: str, reason: str | None = None) -> "ApprovalResult":
        return cls(
            approved=False,
            approval_id=approval_id,
            error_code="approval_denied",
            reason=reason,
        )

    @classmethod
    def expired(cls, approval_id: str) -> "ApprovalResult":
        return cls(
            approved=False,
            approval_id=approval_id,
            error_code="approval_timeout",
            reason="No response within timeout",
        )
