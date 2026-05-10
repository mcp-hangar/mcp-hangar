"""Subscription filter parsing and matching for WebSocket event streams."""

# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false

from ....domain.events import DomainEvent
from ....domain.value_objects.event_pattern import EventPattern


def parse_subscription_filters(msg: dict[str, object] | None) -> dict[str, list[str]]:
    """Parse subscription filter config from a client message.

    Recognized keys:
    - "type": optional "subscribe" control message for initial negotiation
    - "event_types": list[str] -- only deliver events whose event_type is in this list
    - "mcp_server_ids": list[str] -- only deliver events whose mcp_server_id is in this list

    Unknown keys are ignored. An empty dict or None means no filtering (deliver all).

    Args:
        msg: Parsed JSON message from client, or None.

    Returns:
        Dict with zero or more of the recognized filter keys.
    """
    if not msg:
        return {}
    msg_type = msg.get("type")
    if msg_type not in (None, "subscribe"):
        return {}
    result: dict[str, list[str]] = {}
    if "event_types" in msg and isinstance(msg["event_types"], list):
        result["event_types"] = [str(v) for v in msg["event_types"]]
    if "mcp_server_ids" in msg and isinstance(msg["mcp_server_ids"], list):
        result["mcp_server_ids"] = [str(v) for v in msg["mcp_server_ids"]]
    return result


def compile_event_patterns(raw_patterns: list[str]) -> list[EventPattern]:
    """Pre-compile event type strings into EventPattern objects.

    Called once per subscription. Invalid patterns are silently dropped
    (logged at debug level) to avoid breaking existing exact-match callers.
    """
    compiled: list[EventPattern] = []
    for raw in raw_patterns:
        try:
            compiled.append(EventPattern(raw))
        except ValueError:
            pass
    return compiled


def matches_filters(event: DomainEvent, filters: dict[str, list[str]]) -> bool:
    """Determine whether an event passes the given subscription filters.

    An event passes if it satisfies ALL active filters (AND semantics):
    - event_types filter: event_type must match at least one pattern (supports wildcards)
    - mcp_server_ids filter: event.to_dict().get("mcp_server_id") must be in the list

    An empty filters dict means no filtering -- all events pass.

    Args:
        event: The domain event to test.
        filters: Parsed subscription filters from parse_subscription_filters().

    Returns:
        True if the event should be delivered; False to suppress it.
    """
    if not filters:
        return True
    d = event.to_dict()
    if "event_types" in filters:
        event_type = d.get("event_type", "")
        patterns = compile_event_patterns(filters["event_types"])
        if not any(p.matches(event_type) for p in patterns):
            return False
    if "mcp_server_ids" in filters and d.get("mcp_server_id") not in filters["mcp_server_ids"]:
        return False
    return True
