"""Subscription filter parsing and matching for WebSocket event streams."""

# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false

from ....domain.events import DomainEvent


def parse_subscription_filters(msg: dict[str, object] | None) -> dict[str, list[str]]:
    """Parse subscription filter config from a client message.

    Recognized keys:
    - "type": optional "subscribe" control message for initial negotiation
    - "event_types": list[str] -- only deliver events whose event_type is in this list
    - "provider_ids": list[str] -- only deliver events whose provider_id is in this list

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
    if "provider_ids" in msg and isinstance(msg["provider_ids"], list):
        result["provider_ids"] = [str(v) for v in msg["provider_ids"]]
    return result


def matches_filters(event: DomainEvent, filters: dict[str, list[str]]) -> bool:
    """Determine whether an event passes the given subscription filters.

    An event passes if it satisfies ALL active filters (AND semantics):
    - event_types filter: event.to_dict()["event_type"] must be in the list
    - provider_ids filter: event.to_dict().get("provider_id") must be in the list

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
    if "event_types" in filters and d.get("event_type") not in filters["event_types"]:
        return False
    if "provider_ids" in filters and d.get("provider_id") not in filters["provider_ids"]:
        return False
    return True
