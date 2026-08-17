"""Event-store durability posture, read by the readiness report.

This module used to hold a ``HealthEndpoint`` singleton with registrable
``HealthCheck``s -- a registry nothing served (#972, part of the #969 sweep).
The live probes are Starlette routes in ``server/lifecycle.py``, and readiness
reads :func:`get_event_store_durability_status` directly; registering a check
on the singleton wrote to a place no request ever read. What remains is the
one piece with a live reader: the durability posture recorded at bootstrap.
"""

from dataclasses import dataclass


@dataclass
class EventStoreDurabilityStatus:
    """Durability posture of the active event store.

    Recorded at bootstrap so readiness can report when the store is running
    in-memory (non-durable) even though a durable driver was configured -- a
    degraded state in which the audit/event-sourcing trail is lost on restart.
    """

    configured_driver: str
    durable: bool
    degraded: bool
    detail: str = ""


_event_store_durability: EventStoreDurabilityStatus | None = None


def set_event_store_durability_status(status: EventStoreDurabilityStatus | None) -> None:
    """Record the durability posture of the active event store."""
    global _event_store_durability
    _event_store_durability = status


def get_event_store_durability_status() -> EventStoreDurabilityStatus | None:
    """Return the recorded event-store durability posture, if any."""
    return _event_store_durability
