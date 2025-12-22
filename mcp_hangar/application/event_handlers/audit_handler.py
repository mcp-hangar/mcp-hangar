"""Audit event handler for compliance and debugging."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional

from ...domain.events import DomainEvent

logger = logging.getLogger(__name__)


@dataclass
class AuditRecord:
    """Represents an audit log entry."""

    event_id: str
    event_type: str
    occurred_at: datetime
    provider_id: Optional[str]
    data: Dict[str, Any]
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert audit record to dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": (
                self.occurred_at.isoformat() if isinstance(self.occurred_at, datetime) else str(self.occurred_at)
            ),
            "provider_id": self.provider_id,
            "data": self.data,
            "recorded_at": self.recorded_at.isoformat(),
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())


class AuditStore(ABC):
    """Abstract interface for audit log storage."""

    @abstractmethod
    def record(self, audit_record: AuditRecord) -> None:
        """Store an audit record."""
        pass

    @abstractmethod
    def query(
        self,
        provider_id: Optional[str] = None,
        event_type: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[AuditRecord]:
        """Query audit records."""
        pass


class InMemoryAuditStore(AuditStore):
    """In-memory audit store for testing and development."""

    def __init__(self, max_records: int = 10000):
        self._records: List[AuditRecord] = []
        self._max_records = max_records

    def record(self, audit_record: AuditRecord) -> None:
        """Store an audit record."""
        self._records.append(audit_record)
        # Trim old records if over limit
        if len(self._records) > self._max_records:
            self._records = self._records[-self._max_records :]

    def query(
        self,
        provider_id: Optional[str] = None,
        event_type: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[AuditRecord]:
        """Query audit records with optional filters."""
        results = []
        for record in reversed(self._records):  # Most recent first
            if len(results) >= limit:
                break

            # Apply filters
            if provider_id and record.provider_id != provider_id:
                continue
            if event_type and record.event_type != event_type:
                continue
            if since and record.recorded_at < since:
                continue

            results.append(record)

        return results

    def clear(self) -> None:
        """Clear all records (for testing)."""
        self._records.clear()

    @property
    def count(self) -> int:
        """Get number of stored records."""
        return len(self._records)


class LogAuditStore(AuditStore):
    """Audit store that writes to structured logs."""

    def __init__(self, logger_name: str = "audit"):
        self._logger = logging.getLogger(logger_name)

    def record(self, audit_record: AuditRecord) -> None:
        """Log the audit record."""
        self._logger.info(audit_record.to_json())

    def query(
        self,
        provider_id: Optional[str] = None,
        event_type: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[AuditRecord]:
        """Query is not supported for log store."""
        raise NotImplementedError("Log audit store does not support queries")


class AuditEventHandler:
    """
    Event handler that records all events for audit trail.

    Records every domain event with full details for:
    - Compliance requirements
    - Debugging and troubleshooting
    - Historical analysis
    """

    def __init__(
        self,
        store: Optional[AuditStore] = None,
        include_event_types: Optional[List[str]] = None,
        exclude_event_types: Optional[List[str]] = None,
    ):
        """
        Initialize the audit handler.

        Args:
            store: Audit store to use (defaults to in-memory)
            include_event_types: Only record these event types (None = all)
            exclude_event_types: Exclude these event types
        """
        self._store = store or InMemoryAuditStore()
        self._include = set(include_event_types) if include_event_types else None
        self._exclude = set(exclude_event_types) if exclude_event_types else set()

    def handle(self, event: DomainEvent) -> None:
        """Handle a domain event by recording it."""
        event_type = type(event).__name__

        # Check filters
        if self._include is not None and event_type not in self._include:
            return
        if event_type in self._exclude:
            return

        # Extract provider_id if available
        provider_id = getattr(event, "provider_id", None)

        # Create audit record
        record = AuditRecord(
            event_id=event.event_id,
            event_type=event_type,
            occurred_at=event.occurred_at,
            provider_id=provider_id,
            data=event.to_dict(),
        )

        try:
            self._store.record(record)
        except Exception as e:
            logger.error(f"Failed to record audit event: {e}")

    @property
    def store(self) -> AuditStore:
        """Get the audit store."""
        return self._store

    def query(
        self,
        provider_id: Optional[str] = None,
        event_type: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[AuditRecord]:
        """Query audit records."""
        return self._store.query(provider_id=provider_id, event_type=event_type, since=since, limit=limit)
