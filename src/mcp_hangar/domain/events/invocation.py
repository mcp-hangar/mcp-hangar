# pyright: reportExplicitAny=false

"""Tool-invocation events."""

from dataclasses import dataclass, field
from typing import Any

from ..value_objects.compat import accepts_legacy_provider_id
from .base import DomainEvent


# Tool Invocation Events


@accepts_legacy_provider_id
@dataclass
class ToolInvocationRequested(DomainEvent):
    """Published when a tool invocation is requested."""

    mcp_server_id: str
    tool_name: str = ""
    correlation_id: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    identity_context: dict[str, Any] | None = None
    #: SHA-256 of the RAW arguments, computed before they are redacted. The
    #: identity of the payload, kept so the audit trail can still say "this call
    #: and that approval carried the same arguments" without holding the values.
    arguments_hash: str = ""
    schema_version: int = 2

    def __post_init__(self):
        # The hand-written constructor this replaces did `arguments or {}`, so an
        # explicit `arguments=None` produced an empty dict rather than None. Some
        # callers pass the value through from an optional, and every consumer
        # indexes it without a None check.
        if self.arguments is None:
            self.arguments = {}

        # Redacted HERE rather than at the call site, because the call site was
        # not the problem: this event is persisted to SQLite/Postgres and streamed
        # to every `audit:read` holder over `/ws/events`, and it carried the
        # caller's arguments verbatim -- the same dict the approval record beside
        # it has been two-pass redacted since #1130, and the log pipeline prints
        # as `[REDACTED]` (#1168). Doing it in `__post_init__` means no
        # construction site can forget, including ones written later.
        #
        # The hash is taken first and only when absent: `from_dict` rebuilds a
        # stored event by passing every field, and recomputing over the redacted
        # copy would replace the payload's identity with the identity of its
        # redaction -- where two different secrets hash alike.
        from ..security.argument_redaction import hash_arguments, redact_arguments

        if not self.arguments_hash and self.arguments:
            self.arguments_hash = hash_arguments(self.arguments)
        if self.arguments:
            self.arguments = redact_arguments(self.arguments)


@accepts_legacy_provider_id
@dataclass
class ToolInvocationCompleted(DomainEvent):
    """Published when a tool invocation completes successfully."""

    mcp_server_id: str
    tool_name: str = ""
    correlation_id: str = ""
    duration_ms: float = 0.0
    result_size_bytes: int = 0
    identity_context: dict[str, Any] | None = None


@accepts_legacy_provider_id
@dataclass
class ToolInvocationFailed(DomainEvent):
    """Published when a tool invocation fails."""

    mcp_server_id: str
    tool_name: str = ""
    correlation_id: str = ""
    duration_ms: float = 0.0
    error_message: str = ""
    error_type: str = ""
    identity_context: dict[str, Any] | None = None
