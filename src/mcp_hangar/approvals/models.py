"""Approval gate domain models.

ApprovalRequest is the aggregate tracking a single approval lifecycle.
ApprovalResult is the value object returned to the tool wrapper.
"""

from dataclasses import dataclass
from datetime import datetime, UTC
from enum import Enum
from typing import Any


class ApprovalState(str, Enum):  # noqa: UP042
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


@dataclass(init=False)
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
    #: Principal the gated call was made *by*. The record has always named who
    #: decided; without this it never named who asked, which is half of an
    #: attribution chain.
    requested_by: str | None = None
    #: Tenant the gated call originated in. Bound at creation so the resolve and
    #: list surfaces can be scoped to it: without this an approver in one tenant
    #: could see and resolve another tenant's approvals, since authorization is
    #: by permission alone. ``None`` means single-tenant / no tenant context, in
    #: which case scoping is not applied.
    tenant_id: str | None = None

    def __init__(
        self,
        approval_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        arguments_hash: str,
        requested_at: datetime,
        expires_at: datetime,
        state: ApprovalState,
        channel: str,
        provider_id: str | None = None,
        mcp_server_id: str | None = None,
        decided_by: str | None = None,
        decided_at: datetime | None = None,
        reason: str | None = None,
        correlation_id: str = "",
        requested_by: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        resolved_provider_id = mcp_server_id or provider_id
        if resolved_provider_id is None:
            raise TypeError("Missing required argument: mcp_server_id")

        self.approval_id = approval_id
        self.provider_id = resolved_provider_id
        self.tool_name = tool_name
        self.arguments = arguments
        self.arguments_hash = arguments_hash
        self.requested_at = requested_at
        self.expires_at = expires_at
        self.state = state
        self.channel = channel
        self.decided_by = decided_by
        self.decided_at = decided_at
        self.reason = reason
        self.correlation_id = correlation_id
        self.requested_by = requested_by
        self.tenant_id = tenant_id

    @property
    def mcp_server_id(self) -> str:
        return self.provider_id

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
        self.decided_at = datetime.now(UTC)

    def deny(self, decided_by: str, reason: str | None = None) -> None:
        """Transition PENDING -> DENIED."""
        if self.is_terminal():
            raise ValueError(f"Cannot deny request in state {self.state}")
        self.state = ApprovalState.DENIED
        self.decided_by = decided_by
        self.decided_at = datetime.now(UTC)
        self.reason = reason

    def is_expired(self, now: datetime | None = None) -> bool:
        """Whether the approval window has closed.

        ``expires_at`` was persisted, serialised and delivered to the approver
        from the beginning, and read by nothing: the only expiry that ever ran
        was the in-process ``wait()`` timeout, which dies with the waiter.
        """
        return (now or datetime.now(UTC)) >= self.expires_at

    def expire(self) -> None:
        """Transition PENDING -> EXPIRED. Idempotent on already-terminal."""
        if self.state != ApprovalState.PENDING:
            return
        self.state = ApprovalState.EXPIRED
        self.decided_at = datetime.now(UTC)


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
