"""Persistence-backed audit store adapter.

Connects the existing AuditEventHandler with the new
persistent IAuditRepository implementation.
"""

from datetime import datetime, timezone
from typing import List, Optional
import asyncio

from ...domain.contracts.persistence import (
    AuditAction,
    AuditEntry,
    IAuditRepository,
)
from ...logging_config import get_logger
from .audit_handler import AuditRecord, AuditStore

logger = get_logger(__name__)


class PersistentAuditStore(AuditStore):
    """Audit store backed by IAuditRepository.

    Bridges the synchronous AuditEventHandler with the
    async IAuditRepository for persistent storage.
    """

    def __init__(self, repository: IAuditRepository):
        """Initialize with audit repository.

        Args:
            repository: Async audit repository for persistence
        """
        self._repo = repository
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create event loop for async operations."""
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            # No running loop - create one
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
            return self._loop

    def record(self, audit_record: AuditRecord) -> None:
        """Store an audit record.

        Converts AuditRecord to AuditEntry and persists asynchronously.

        Args:
            audit_record: Record to store
        """
        entry = self._record_to_entry(audit_record)

        try:
            loop = self._get_loop()
            if loop.is_running():
                # Schedule as task if loop is running
                asyncio.create_task(self._async_record(entry))
            else:
                # Run synchronously if no loop
                loop.run_until_complete(self._async_record(entry))
        except Exception as e:
            logger.error(f"Failed to persist audit record: {e}")

    async def _async_record(self, entry: AuditEntry) -> None:
        """Async record method."""
        try:
            await self._repo.append(entry)
        except Exception as e:
            logger.error(f"Failed to persist audit entry: {e}")

    def query(
        self,
        provider_id: Optional[str] = None,
        event_type: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[AuditRecord]:
        """Query audit records.

        Args:
            provider_id: Filter by provider ID
            event_type: Filter by event type (entity_type in new model)
            since: Filter records after this time
            limit: Maximum records to return

        Returns:
            List of audit records
        """
        try:
            loop = self._get_loop()

            if provider_id:
                coro = self._repo.get_by_entity(
                    entity_id=provider_id,
                    entity_type="provider",
                    limit=limit,
                )
            elif since:
                coro = self._repo.get_by_time_range(
                    start=since,
                    end=datetime.now(timezone.utc),
                    entity_type=event_type,
                    limit=limit,
                )
            else:
                # Get recent entries
                coro = self._repo.get_by_time_range(
                    start=datetime(2000, 1, 1, tzinfo=timezone.utc),
                    end=datetime.now(timezone.utc),
                    entity_type=event_type,
                    limit=limit,
                )

            if loop.is_running():
                # Can't run sync from async context
                logger.warning("Cannot query audit in async context synchronously")
                return []
            else:
                entries = loop.run_until_complete(coro)
                return [self._entry_to_record(e) for e in entries]

        except Exception as e:
            logger.error(f"Failed to query audit records: {e}")
            return []

    def _record_to_entry(self, record: AuditRecord) -> AuditEntry:
        """Convert AuditRecord to AuditEntry.

        Args:
            record: Legacy audit record

        Returns:
            New AuditEntry format
        """
        # Map event type to action
        action = self._map_event_to_action(record.event_type)

        # Handle timestamp
        if isinstance(record.occurred_at, datetime):
            timestamp = record.occurred_at
        elif isinstance(record.occurred_at, (int, float)):
            timestamp = datetime.fromtimestamp(record.occurred_at, tz=timezone.utc)
        else:
            timestamp = datetime.now(timezone.utc)

        return AuditEntry(
            entity_id=record.provider_id or "_unknown",
            entity_type="provider",
            action=action,
            timestamp=timestamp,
            actor="system",
            metadata={
                "event_id": record.event_id,
                "event_type": record.event_type,
                "data": record.data,
            },
        )

    def _entry_to_record(self, entry: AuditEntry) -> AuditRecord:
        """Convert AuditEntry to AuditRecord.

        Args:
            entry: New audit entry

        Returns:
            Legacy AuditRecord format
        """
        event_id = entry.metadata.get("event_id", str(entry.timestamp.timestamp()))
        event_type = entry.metadata.get("event_type", entry.action.value)
        data = entry.metadata.get("data", {})

        return AuditRecord(
            event_id=event_id,
            event_type=event_type,
            occurred_at=entry.timestamp,
            provider_id=entry.entity_id if entry.entity_id != "_unknown" else None,
            data=data,
            recorded_at=entry.timestamp,
        )

    def _map_event_to_action(self, event_type: str) -> AuditAction:
        """Map domain event type to audit action.

        Args:
            event_type: Domain event class name

        Returns:
            Corresponding AuditAction
        """
        mapping = {
            "ProviderStarted": AuditAction.STARTED,
            "ProviderStopped": AuditAction.STOPPED,
            "ProviderDegraded": AuditAction.DEGRADED,
            "ProviderStateChanged": AuditAction.STATE_CHANGED,
            "ProviderRegistered": AuditAction.CREATED,
            "ProviderUnregistered": AuditAction.DELETED,
            "ToolInvocationCompleted": AuditAction.UPDATED,
            "ToolInvocationFailed": AuditAction.STATE_CHANGED,
            "HealthCheckPassed": AuditAction.RECOVERED,
            "HealthCheckFailed": AuditAction.DEGRADED,
        }
        return mapping.get(event_type, AuditAction.UPDATED)


def create_persistent_audit_handler(
    repository: IAuditRepository,
    include_event_types: Optional[List[str]] = None,
    exclude_event_types: Optional[List[str]] = None,
):
    """Create AuditEventHandler with persistent storage.

    Factory function to create an AuditEventHandler backed
    by an IAuditRepository for durable storage.

    Args:
        repository: Audit repository for persistence
        include_event_types: Only record these event types
        exclude_event_types: Exclude these event types

    Returns:
        Configured AuditEventHandler
    """
    from .audit_handler import AuditEventHandler

    store = PersistentAuditStore(repository)
    return AuditEventHandler(
        store=store,
        include_event_types=include_event_types,
        exclude_event_types=exclude_event_types,
    )

