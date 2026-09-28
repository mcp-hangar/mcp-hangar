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
        super().__post_init__()


@accepts_legacy_provider_id
@dataclass
class ToolInvocationCompleted(DomainEvent):
    """Published when a tool invocation completes successfully.

    ``mcp_server_id`` is the server that ran the call: for a group call, the
    selected member. ``logical_target`` is what the caller named -- the group
    for a group call, the server itself otherwise -- which is what audit is
    keyed on (ADR-029 s5, #1594). Empty on an event persisted before #1594 and
    on a call no caller named; a reader then falls back to ``mcp_server_id``.
    """

    mcp_server_id: str
    tool_name: str = ""
    correlation_id: str = ""
    duration_ms: float = 0.0
    result_size_bytes: int = 0
    identity_context: dict[str, Any] | None = None
    logical_target: str = ""


@accepts_legacy_provider_id
@dataclass
class ToolInvocationFailed(DomainEvent):
    """Published when a tool invocation fails.

    ``mcp_server_id`` and ``logical_target`` as on ``ToolInvocationCompleted``.
    """

    mcp_server_id: str
    tool_name: str = ""
    correlation_id: str = ""
    duration_ms: float = 0.0
    error_message: str = ""
    error_type: str = ""
    identity_context: dict[str, Any] | None = None
    logical_target: str = ""


@dataclass
class ToolCallRefused(DomainEvent):
    """Published when Hangar refuses a tool call before it reaches the upstream (#1582).

    A refusal returns before the aggregate invokes anything, so neither
    ``ToolInvocationCompleted`` nor ``ToolInvocationFailed`` exists for it, and
    audit -- which exported only those two -- never saw the decisions an
    auditor most needs. This is the one record of a refused call: a gate of the
    batch executor that said ``deny``, the ``tool:invoke`` check before the
    gates (``gate="authorization"``), or an L7 verdict raised at dispatch.

    Every field is an identifier, a number or a bounded code, never text a gate
    was handed: an approver's or a validator's reason stays out (#1276). A gate
    refusal carries ``gate`` and ``gate_reason`` (ADR-029's ``hangar.gate.*``);
    an L7 refusal carries the ``l7_*`` verdict fields instead (``hangar.l7.*``).

    Attributes:
        mcp_server_id: The logical target the caller named (ADR-029 s5).
        tool_name: The tool the caller named.
        correlation_id: The refused call's id.
        identity_context: The caller's ``IdentityContext.to_dict()``, if any.
        gate: The refusing gate, without its ``_gate_`` prefix, or ``authorization``.
        gate_reason: The gate's bounded reason code, when it has one.
        l7_verdict: ``deny`` or ``require_approval``, for an L7 refusal.
        l7_mode: The policy's lowercased mode.
        l7_rule_kind: Which part of the policy decided.
        l7_policy_id: The content hash of the policy.
        elapsed_ms: Time from the call's start to its refusal.
        route_backend: The server the call was routed to, once its target was
            resolved: the selected member for a group, the server itself
            otherwise (ADR-029's ``hangar.route.backend``, #1594). None when
            the call was refused before a backend was chosen.
    """

    mcp_server_id: str
    tool_name: str = ""
    correlation_id: str = ""
    identity_context: dict[str, Any] | None = None
    gate: str | None = None
    gate_reason: str | None = None
    l7_verdict: str | None = None
    l7_mode: str | None = None
    l7_rule_kind: str | None = None
    l7_policy_id: str | None = None
    elapsed_ms: float = 0.0
    route_backend: str | None = None
