"""Persistence-backed audit store adapter.

Connects the existing AuditEventHandler with the new
persistent IAuditRepository implementation.
"""

import asyncio
from datetime import datetime, UTC

from ...domain.contracts.persistence import AuditAction, AuditEntry, IAuditRepository
from ...logging_config import get_logger
from ..ports.async_task import IAsyncTaskSubmitter
from .audit_handler import AuditRecord, AuditStore

logger = get_logger(__name__)


class PersistentAuditStore(AuditStore):
    """Audit store backed by IAuditRepository.

    Bridges the synchronous AuditEventHandler with the
    async IAuditRepository for persistent storage.

    Uses AsyncExecutor for efficient background persistence
    without blocking the main thread.
    """

    def __init__(self, repository: IAuditRepository, async_task: IAsyncTaskSubmitter | None = None):
        """Initialize with audit repository.

        Args:
            repository: Async audit repository for persistence
            async_task: Async task submitter for background persistence.
                Falls back to global AsyncExecutor singleton if None.
        """
        self._repo = repository
        self._async_task = async_task

    def record(self, audit_record: AuditRecord) -> None:
        """Store an audit record asynchronously.

        Converts AuditRecord to AuditEntry and persists using
        background executor.

        Args:
            audit_record: Record to store
        """
        entry = self._record_to_entry(audit_record)

        if self._async_task is not None:
            self._async_task.submit(
                self._async_record(entry),
                on_error=lambda e: logger.error(f"Failed to persist audit record: {e}"),
            )
        else:
            from ...infrastructure.async_executor import submit_async

            submit_async(
                self._async_record(entry),
                on_error=lambda e: logger.error(f"Failed to persist audit record: {e}"),
            )

    async def _async_record(self, entry: AuditEntry) -> None:
        """Async record method."""
        try:
            await self._repo.append(entry)
        except Exception as e:  # noqa: BLE001 -- fault-barrier: persistence failure must not crash audit pipeline
            logger.error(f"Failed to persist audit entry: {e}")

    def query(
        self,
        mcp_server_id: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        caller_user_id: str | None = None,
    ) -> list[AuditRecord]:
        """Query audit records.

        Args:
            mcp_server_id: Filter by mcp_server ID
            event_type: Filter by event type (entity_type in new model)
            since: Filter records after this time
            limit: Maximum records to return
            caller_user_id: Filter by caller user ID

        Returns:
            List of audit records
        """
        try:
            if caller_user_id:
                coro = self._repo.get_by_caller(
                    caller_user_id=caller_user_id,
                    limit=limit,
                )
            elif mcp_server_id:
                coro = self._repo.get_by_entity(
                    entity_id=mcp_server_id,
                    entity_type="mcp_server",
                    limit=limit,
                )
            elif since:
                coro = self._repo.get_by_time_range(
                    start=since,
                    end=datetime.now(UTC),
                    entity_type=event_type,
                    limit=limit,
                )
            else:
                # Get recent entries
                coro = self._repo.get_by_time_range(
                    start=datetime(2000, 1, 1, tzinfo=UTC),
                    end=datetime.now(UTC),
                    entity_type=event_type,
                    limit=limit,
                )

            # Check if we're in an async context
            try:
                asyncio.get_running_loop()
                # We're in async context - can't run sync query
                logger.warning("Cannot query audit in async context synchronously")
                return []
            except RuntimeError:
                # Not in async context - safe to use asyncio.run()
                entries = asyncio.run(coro)
                return [self._entry_to_record(e) for e in entries]

        except Exception as e:  # noqa: BLE001 -- fault-barrier: query failure returns empty list, not crash
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
        elif isinstance(record.occurred_at, int | float):
            timestamp = datetime.fromtimestamp(record.occurred_at, tz=UTC)
        else:
            timestamp = datetime.now(UTC)

        # Use caller identity as actor if available, fall back to "system"
        actor = record.caller_user_id or "system"

        return AuditEntry(
            entity_id=record.mcp_server_id or "_unknown",
            entity_type="mcp_server",
            action=action,
            timestamp=timestamp,
            actor=actor,
            metadata={
                "event_id": record.event_id,
                "event_type": record.event_type,
                "data": record.data,
            },
            caller_user_id=record.caller_user_id,
            caller_agent_id=record.caller_agent_id,
            caller_session_id=record.caller_session_id,
            caller_principal_type=record.caller_principal_type,
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
            mcp_server_id=entry.entity_id if entry.entity_id != "_unknown" else None,
            data=data,
            recorded_at=entry.timestamp,
            caller_user_id=entry.caller_user_id,
            caller_agent_id=entry.caller_agent_id,
            caller_session_id=entry.caller_session_id,
            caller_principal_type=entry.caller_principal_type,
        )

    def _map_event_to_action(self, event_type: str) -> AuditAction:
        """Map domain event type to audit action.

        Args:
            event_type: Domain event class name

        Returns:
            Corresponding AuditAction
        """
        mapping = {
            "McpServerStarted": AuditAction.STARTED,
            "McpServerStopped": AuditAction.STOPPED,
            "McpServerDegraded": AuditAction.DEGRADED,
            "McpServerStateChanged": AuditAction.STATE_CHANGED,
            "McpServerRegistered": AuditAction.CREATED,
            "McpServerUnregistered": AuditAction.DELETED,
            "ToolInvocationRequested": AuditAction.TOOL_INVOKED,
            "ToolInvocationCompleted": AuditAction.TOOL_INVOKED,
            "ToolInvocationFailed": AuditAction.TOOL_INVOKED,
            "HealthCheckPassed": AuditAction.RECOVERED,
            "HealthCheckFailed": AuditAction.DEGRADED,
        }
        return mapping.get(event_type, AuditAction.UPDATED)


def create_persistent_audit_handler(
    repository: IAuditRepository,
    include_event_types: list[str] | None = None,
    exclude_event_types: list[str] | None = None,
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
