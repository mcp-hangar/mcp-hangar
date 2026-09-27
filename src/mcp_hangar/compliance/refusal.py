"""What the compliance formats share about a tool call's outcome (#1582).

A refused call reaches the exporters with ``status="denied"``. Each format used
to map a status it did not know to ``ToolInvocationRequested``, so a refusal
read as a request that had merely been made. It is its own event type now, the
same in all four formats.

The refusal's bounded attributes (ADR-029's ``hangar.gate.*`` and
``hangar.l7.*``) are written under one short key per format. A key outside that
vocabulary is dropped rather than passed through: the formats carry codes, never
text a gate was handed (#1276).
"""

from collections.abc import Mapping

from mcp_hangar.observability.conventions import L7, Gate

#: The event type of a call Hangar refused before it reached the upstream.
TOOL_INVOCATION_DENIED = "ToolInvocationDenied"

#: (convention key, key in ``AuditRecord.data`` and JSON lines, key in CEF, LEEF and syslog).
REFUSAL_FIELDS: tuple[tuple[str, str, str], ...] = (
    (Gate.NAME, "gate", "gate"),
    (Gate.REASON, "gate_reason", "gateReason"),
    (L7.VERDICT, "l7_verdict", "l7Verdict"),
    (L7.MODE, "l7_mode", "l7Mode"),
    (L7.RULE_KIND, "l7_rule_kind", "l7RuleKind"),
    (L7.POLICY_ID, "l7_policy_id", "l7PolicyId"),
)


#: The ``AuditRecord.data`` key of the server a call was routed to (#1594): the
#: selected member for a group call, whose record names the group as its
#: server; the server itself on a standalone call. ADR-029's
#: ``hangar.route.backend``. Each format writes it under its own convention:
#: ``route_backend`` in JSON lines, ``routeBackend`` in LEEF and syslog, and
#: the labelled ``flexString1`` in CEF, whose six ``cs`` slots are all taken.
ROUTE_BACKEND = "route_backend"


def event_type_for_status(status: str) -> str:
    """The audit event type a tool call's *status* is written as."""
    if status in ("success", "completed"):
        return "ToolInvocationCompleted"
    if status in ("error", "failure", "failed"):
        return "ToolInvocationFailed"
    if status == "denied":
        return TOOL_INVOCATION_DENIED
    return "ToolInvocationRequested"


def refusal_data(refusal: Mapping[str, str] | None) -> dict[str, str]:
    """*refusal* under its ``AuditRecord.data`` keys; anything outside the vocabulary is left out."""
    given = refusal or {}
    return {data_key: given[key] for key, data_key, _wire in REFUSAL_FIELDS if given.get(key)}


def refusal_wire_fields(data: Mapping[str, object]) -> list[tuple[str, str]]:
    """The refusal fields held in *data*, as (wire key, value) in vocabulary order."""
    return [(wire, str(data[data_key])) for _key, data_key, wire in REFUSAL_FIELDS if data.get(data_key)]
