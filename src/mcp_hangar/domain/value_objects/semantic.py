"""Value objects for semantic analysis (call sequence pattern detection).

These value objects represent events in a tool invocation sequence and the
results of matching detection rules against those sequences. Used by the
pattern engine to track per-session call windows and report matches.

MIT licensed -- part of the core domain model.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DetectionSeverity(Enum):
    """Severity level for a detection rule match.

    Ordered from least to most severe. Used by rules and response actions
    to determine the appropriate reaction to a pattern match.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ResponseActionType(Enum):
    """Type of automated response action to take on a rule match.

    Defines the escalation ladder from passive notification to active
    enforcement. Each level includes all behaviors of lower levels
    (e.g., THROTTLE also emits an alert).
    """

    ALERT = "alert"
    THROTTLE = "throttle"
    SUSPEND = "suspend"
    BLOCK = "block"


@dataclass(frozen=True)
class CallSequenceEvent:
    """A single event in a tool invocation sequence.

    Represents one tool call within a session's sliding window. The pattern
    engine maintains a per-session list of these events and evaluates
    detection rules against the sequence.

    Attributes:
        tool_name: Name of the invoked tool (e.g., "read_file").
        provider_id: Provider that executed the tool.
        args_fingerprint: Deterministic hash of the tool arguments.
            Used for argument-level matching without storing raw arguments.
        timestamp: Unix timestamp of the invocation (seconds since epoch).
        session_id: Session identifier. Derived from CallerIdentity.session_id
            or correlation_id when session_id is not available.
        correlation_id: Request correlation ID for audit trail linkage.
    """

    tool_name: str
    provider_id: str
    args_fingerprint: str
    timestamp: float
    session_id: str
    correlation_id: str | None = None

    @staticmethod
    def compute_args_fingerprint(arguments: dict[str, Any]) -> str:
        """Compute a deterministic fingerprint of tool arguments.

        Produces a stable SHA-256 hex digest (first 16 chars) from the
        JSON-serialized arguments dict with sorted keys. This allows
        rules to match on argument patterns without storing raw values.

        Args:
            arguments: Tool invocation arguments dict.

        Returns:
            16-character hex digest of the arguments.
        """
        serialized = json.dumps(arguments, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class PatternMatchResult:
    """Result of a detection rule matching against a call sequence.

    Produced by IDetectionRule.evaluate() when the session's call window
    contains a sequence of events matching the rule's step matchers.

    Attributes:
        rule_id: Unique identifier of the matched rule.
        rule_name: Human-readable name of the matched rule.
        severity: Severity of the detection (from rule definition).
        matched_events: The CallSequenceEvent instances that formed the
            match, in the order they appeared in the sequence.
        recommended_action: The response action type recommended by the
            rule (alert, throttle, suspend, block).
        metadata: Additional context from the rule evaluation (e.g.,
            which step matchers fired, time span of the match).
    """

    rule_id: str
    rule_name: str
    severity: DetectionSeverity
    matched_events: tuple[CallSequenceEvent, ...]
    recommended_action: ResponseActionType = ResponseActionType.ALERT
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def session_id(self) -> str | None:
        """Session ID from the first matched event, if available."""
        if self.matched_events:
            return self.matched_events[0].session_id
        return None

    @property
    def provider_id(self) -> str | None:
        """Provider ID from the first matched event, if available."""
        if self.matched_events:
            return self.matched_events[0].provider_id
        return None

    @property
    def matched_tool_names(self) -> list[str]:
        """Ordered list of tool names in the matched sequence."""
        return [e.tool_name for e in self.matched_events]
