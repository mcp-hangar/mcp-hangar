"""Event-store durability posture, read by the readiness report.

This module used to hold a ``HealthEndpoint`` singleton with registrable
``HealthCheck``s -- a registry nothing served (#972, part of the #969 sweep).
The live probes are Starlette routes in ``server/lifecycle.py``, and readiness
reads :func:`get_event_store_durability_status` directly; registering a check
on the singleton wrote to a place no request ever read. What remains are the
pieces with a live reader: the durability posture recorded at bootstrap, and
the compliance file feed's write health (#1701).
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


@dataclass
class ComplianceExportStatus:
    """The compliance (SIEM) file feed's write health, read by readiness and `hangar_health` (#1701).

    Updated by the feed on every write. ``failing`` is true from a failed write
    until the next one that succeeds; ``failures`` counts every record dropped
    since boot, as ``mcp_hangar_compliance_export_failures_total`` does.
    """

    format: str
    output: str
    failing: bool = False
    failures: int = 0
    last_reason: str | None = None

    def report(self, *, with_output: bool) -> dict[str, object]:
        """The health field. ``with_output=False`` leaves the path out, for an unauthenticated reader."""
        body: dict[str, object] = {
            "status": "degraded" if self.failing else "healthy",
            "format": self.format,
            "failures": self.failures,
            "last_reason": self.last_reason,
        }
        if with_output:
            body["output"] = self.output
        return body


_compliance_export: ComplianceExportStatus | None = None


def set_compliance_export_status(status: ComplianceExportStatus | None) -> None:
    """Record the configured compliance file feed's status; None when no file feed is configured."""
    global _compliance_export
    _compliance_export = status


def get_compliance_export_status() -> ComplianceExportStatus | None:
    """Return the configured compliance file feed's status, if any."""
    return _compliance_export
