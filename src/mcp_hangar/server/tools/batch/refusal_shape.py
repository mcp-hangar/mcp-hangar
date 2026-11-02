"""What a caller is told when a tool it may see is refused (ADR-029 s12, #1918).

The gates decide; this module says how a decision reads on the other side of the
call. It holds no policy: `disposition` only sorts what `_gate_decision` already
said, and a refusal it does not shape keeps the result it had.

Hangar writes its own keys, not `io.modelcontextprotocol/`: SEP-3313 and SEP-2643
are drafts, and that prefix is the specification's (ADR-029 s12.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

#: The `_meta` key of the classification every shaped refusal carries.
TOOL_FAILURE_KEY = "io.mcp-hangar/toolFailure"
#: The `_meta` key of the SEP-2643 envelope an authorization denial also carries.
AUTHORIZATION_DENIAL_KEY = "io.mcp-hangar/authorizationDenial"


class Disposition(StrEnum):
    """What a stage of `_GATES` tells the caller when it stops a call."""

    #: A tool the caller may see, refused on purpose: the shape below.
    SHAPED = "shaped"
    #: A tool the caller may not see: indistinguishable from one that does not exist (#905).
    HIDDEN = "hidden"
    #: No governance decision: a cancellation, a spent budget, a failed start.
    NOT_A_REFUSAL = "not_a_refusal"


#: Every stage of `_GATES`. A test holds this to `_GATES`, so a new gate has to
#: decide what its caller is told (ADR-029 s12.5).
GATE_DISPOSITIONS: dict[str, Disposition] = {
    "cancelled_before_execution": Disposition.NOT_A_REFUSAL,
    "global_timeout": Disposition.NOT_A_REFUSAL,
    "resolve_target": Disposition.HIDDEN,
    "tool_access": Disposition.HIDDEN,
    "withdrawal": Disposition.HIDDEN,
    "digest_pin": Disposition.SHAPED,
    "circuit_breaker": Disposition.SHAPED,
    "validators": Disposition.SHAPED,
    "tenant_budget": Disposition.SHAPED,
    "approval": Disposition.SHAPED,
    # By reason: see `_COLD_START_SHAPED`.
    "cold_start": Disposition.NOT_A_REFUSAL,
    "deferred_digest_pin": Disposition.SHAPED,
    "cancelled_after_cold_start": Disposition.NOT_A_REFUSAL,
}

#: The reasons a cold start refuses on purpose. A failed start is not one of them.
_COLD_START_SHAPED = frozenset({"not_revived_by_calls", "circuit_open", "upstream_peer_key_mismatch"})

#: What waiting clears (ADR-029 s12.2): `class: reliability`, which a client may retry.
_CLEARS_BY_WAITING = frozenset({"circuit_open", "concurrency", "rate"})

#: The SEP-2643 `remediation` of a denial; only these reasons carry the envelope.
_AUTHORIZATION_REMEDIATION = {"approval_denied": "unavailable"}


@dataclass(frozen=True)
class RefusalShape:
    """The text and the `_meta` of one refusal."""

    text: str
    meta: dict[str, Any]


def disposition(gate: str, reason: str | None) -> Disposition:
    """How *gate*, having said `deny` for *reason*, reads to its caller."""
    if gate == "cold_start" and reason in _COLD_START_SHAPED:
        return Disposition.SHAPED
    return GATE_DISPOSITIONS[gate]


def _next_step(gate: str, reason: str | None, server: str, event_id: str) -> str:
    if reason == "digest_mismatch":
        return f"Review the change, then run: mcp-hangar pin --write --server {server}"
    if reason in _CLEARS_BY_WAITING:
        return "Try again later"
    return f"See why: mcp-hangar why {event_id}"


def shape_refusal(
    gate: str,
    reason: str | None,
    *,
    server: str,
    event_id: str,
    retry_after_s: float | None = None,
) -> RefusalShape:
    """The refusal *gate* gives for *reason*, correlated to the event *event_id*.

    *reason* is omitted from the text and the code when the gate cannot say why:
    section 5 of ADR-029 forbids inventing one. The text here is the bounded
    first line; the gate's own message, which can carry an approver's or a
    validator's words, goes below it and is not for a client to parse.
    """
    head = f"Refused by MCP Hangar ({gate}): {reason}" if reason else f"Refused by MCP Hangar ({gate})"
    failure: dict[str, Any] = {
        "class": "governance",
        "code": f"{gate}.{reason}" if reason else gate,
        "correlationId": event_id,
    }
    if reason in _CLEARS_BY_WAITING:
        failure["class"] = "reliability"
        failure["retryable"] = True
        if retry_after_s is not None:
            not_before = datetime.now(UTC) + timedelta(seconds=retry_after_s)
            failure["retryNotBefore"] = int(not_before.timestamp() * 1000)
    meta: dict[str, Any] = {TOOL_FAILURE_KEY: failure}
    remediation = _AUTHORIZATION_REMEDIATION.get(reason or "")
    if remediation is not None:
        meta[AUTHORIZATION_DENIAL_KEY] = {"reason": "insufficient_authorization", "remediation": remediation}
    return RefusalShape(f"{head}. {_next_step(gate, reason, server, event_id)}", meta)
