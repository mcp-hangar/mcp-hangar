# pyright: reportExplicitAny=false

"""Semantic analysis and detection events."""

from dataclasses import dataclass, field
from typing import Any

from .base import DomainEvent


# Semantic analysis events (Phase 57-59 -- v10.0 Semantic Analysis Alpha)
# ---------------------------------------------------------------------------


@dataclass
class DetectionRuleMatched(DomainEvent):
    """Published when a session's call sequence matches a detection rule.

    Emitted by the semantic analysis engine after evaluating a session's
    sliding window of tool invocations against the active rule set. One
    event per rule match (a single invocation can trigger multiple rules).

    This event is consumed by the DetectionRuleMatchedEventHandler which
    increments Prometheus counters and creates OTLP spans.

    Attributes:
        rule_id: Unique identifier of the matched rule (e.g. "credential-exfiltration").
        rule_name: Human-readable rule name.
        severity: Detection severity ("critical", "high", "medium", "low").
        session_id: Session that triggered the match.
        mcp_server_id: McpServer involved in the final matching tool call.
        matched_tools: Tuple of tool names that formed the matched sequence.
        recommended_action: Response action from the rule ("alert", "throttle", "suspend", "block").
        metadata: Additional match context (timestamps, args fingerprints, etc.).
        schema_version: Event schema version.
    """

    rule_id: str
    rule_name: str
    severity: str
    session_id: str
    mcp_server_id: str
    matched_tools: tuple[str, ...] = field(default_factory=tuple)
    recommended_action: str = "alert"
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    @property
    def provider_id(self) -> str:
        import warnings

        warnings.warn(
            "provider_id is deprecated; use mcp_server_id instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.mcp_server_id


@dataclass
class EnforcementActionTaken(DomainEvent):
    """Published when an automated response action is executed for a rule match.

    Emitted by the ResponseOrchestrator after executing an IResponseAction
    (alert, throttle, suspend, block) in response to a DetectionRuleMatched
    event. One event per action execution.

    This event is consumed by the EnforcementActionTakenEventHandler which
    increments Prometheus counters and creates OTLP spans with
    ``mcp.enforcement.action`` attributes.

    Attributes:
        action: The response action type that was executed ("alert", "throttle",
            "suspend", "block").
        rule_id: Identifier of the detection rule that triggered this action.
        session_id: Session that triggered the original detection.
        mcp_server_id: McpServer involved in the matched sequence.
        matched_tools: Tuple of tool names from the matched sequence.
        detail: Human-readable description of the action taken.
        metadata: Additional context (TTL, rate limit params, etc.).
        schema_version: Event schema version.
    """

    action: str
    rule_id: str
    session_id: str
    mcp_server_id: str
    matched_tools: tuple[str, ...] = field(default_factory=tuple)
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1


# =============================================================================


@dataclass
class SessionSuspended(DomainEvent):
    """A session was suspended, and every replica has to act on it.

    Suspension used to be a change to one process's memory. With more than one
    replica that is a bypass rather than a block: the session is refused by the
    pod that suspended it and served by the other two, so retrying the request
    is enough to get through. It travels as an event so the decision reaches the
    whole fleet -- the subject is the session, not the pod that happened to take
    the request that triggered it.

    Attributes:
        session_id: The session being suspended.
        reason: Why, for the audit trail and the operator.
        source: What asked -- a detection rule id, or "api" for an operator.
    """

    session_id: str
    reason: str = ""
    source: str = "api"


@dataclass
class SessionUnsuspended(DomainEvent):
    """A suspension was lifted, on every replica.

    Needed for the same reason as its counterpart, and slightly more urgently:
    a lift that reaches one replica leaves the session refused by the other two,
    which looks to the caller like an intermittent block nobody can explain.

    Attributes:
        session_id: The session being released.
        reason: Why, for the audit trail.
        source: What asked.
    """

    session_id: str
    reason: str = ""
    source: str = "api"
