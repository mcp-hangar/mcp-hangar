"""Audit service for tracking and logging operations.

Domain service responsible for creating audit entries for
provider lifecycle events and operations.
"""

from datetime import datetime
from typing import Any

from ..contracts.persistence import AuditAction, AuditEntry, IAuditRepository
from ..events import (
    DomainEvent,
    ProviderDegraded,
    ProviderStarted,
    ProviderStateChanged,
    ProviderStopped,
    ToolInvocationCompleted,
    ToolInvocationFailed,
)
from ..value_objects import ProviderState


class AuditService:
    """Domain service for audit operations.

    Creates audit entries from domain events and operations,
    delegating persistence to the audit repository.
    """

    def __init__(self, audit_repository: IAuditRepository):
        """Initialize audit service.

        Args:
            audit_repository: Repository for persisting audit entries
        """
        self._repo = audit_repository

    async def record_provider_created(
        self,
        provider_id: str,
        config: dict[str, Any],
        actor: str = "system",
        correlation_id: str | None = None,
    ) -> None:
        """Record provider creation audit entry.

        Args:
            provider_id: Provider identifier
            config: Provider configuration
            actor: Who created the provider
            correlation_id: Optional correlation ID for tracing
        """
        await self._repo.append(
            AuditEntry(
                entity_id=provider_id,
                entity_type="provider",
                action=AuditAction.CREATED,
                timestamp=datetime.utcnow(),
                actor=actor,
                new_state=config,
                correlation_id=correlation_id,
            )
        )

    async def record_provider_updated(
        self,
        provider_id: str,
        old_config: dict[str, Any],
        new_config: dict[str, Any],
        actor: str = "system",
        correlation_id: str | None = None,
    ) -> None:
        """Record provider configuration update.

        Args:
            provider_id: Provider identifier
            old_config: Previous configuration
            new_config: New configuration
            actor: Who updated the provider
            correlation_id: Optional correlation ID
        """
        await self._repo.append(
            AuditEntry(
                entity_id=provider_id,
                entity_type="provider",
                action=AuditAction.UPDATED,
                timestamp=datetime.utcnow(),
                actor=actor,
                old_state=old_config,
                new_state=new_config,
                correlation_id=correlation_id,
            )
        )

    async def record_provider_deleted(
        self,
        provider_id: str,
        config: dict[str, Any],
        actor: str = "system",
        correlation_id: str | None = None,
    ) -> None:
        """Record provider deletion.

        Args:
            provider_id: Provider identifier
            config: Provider configuration at time of deletion
            actor: Who deleted the provider
            correlation_id: Optional correlation ID
        """
        await self._repo.append(
            AuditEntry(
                entity_id=provider_id,
                entity_type="provider",
                action=AuditAction.DELETED,
                timestamp=datetime.utcnow(),
                actor=actor,
                old_state=config,
                correlation_id=correlation_id,
            )
        )

    async def record_provider_started(
        self,
        provider_id: str,
        mode: str,
        tools_count: int,
        startup_duration_ms: float,
        actor: str = "system",
        correlation_id: str | None = None,
    ) -> None:
        """Record provider start event.

        Args:
            provider_id: Provider identifier
            mode: Provider mode (subprocess, docker, remote)
            tools_count: Number of tools discovered
            startup_duration_ms: Time to start in milliseconds
            actor: Who started the provider
            correlation_id: Optional correlation ID
        """
        await self._repo.append(
            AuditEntry(
                entity_id=provider_id,
                entity_type="provider",
                action=AuditAction.STARTED,
                timestamp=datetime.utcnow(),
                actor=actor,
                new_state={
                    "state": ProviderState.READY.value,
                    "mode": mode,
                    "tools_count": tools_count,
                },
                metadata={
                    "startup_duration_ms": startup_duration_ms,
                },
                correlation_id=correlation_id,
            )
        )

    async def record_provider_stopped(
        self,
        provider_id: str,
        reason: str,
        actor: str = "system",
        correlation_id: str | None = None,
    ) -> None:
        """Record provider stop event.

        Args:
            provider_id: Provider identifier
            reason: Reason for stopping (shutdown, idle, error, degraded)
            actor: Who stopped the provider
            correlation_id: Optional correlation ID
        """
        await self._repo.append(
            AuditEntry(
                entity_id=provider_id,
                entity_type="provider",
                action=AuditAction.STOPPED,
                timestamp=datetime.utcnow(),
                actor=actor,
                old_state={"state": "running"},
                new_state={"state": ProviderState.COLD.value},
                metadata={"reason": reason},
                correlation_id=correlation_id,
            )
        )

    async def record_provider_degraded(
        self,
        provider_id: str,
        consecutive_failures: int,
        total_failures: int,
        reason: str,
        actor: str = "system",
        correlation_id: str | None = None,
    ) -> None:
        """Record provider degradation event.

        Args:
            provider_id: Provider identifier
            consecutive_failures: Number of consecutive failures
            total_failures: Total failure count
            reason: Reason for degradation
            actor: Who caused the degradation (usually 'system')
            correlation_id: Optional correlation ID
        """
        await self._repo.append(
            AuditEntry(
                entity_id=provider_id,
                entity_type="provider",
                action=AuditAction.DEGRADED,
                timestamp=datetime.utcnow(),
                actor=actor,
                new_state={"state": ProviderState.DEGRADED.value},
                metadata={
                    "consecutive_failures": consecutive_failures,
                    "total_failures": total_failures,
                    "reason": reason,
                },
                correlation_id=correlation_id,
            )
        )

    async def record_state_change(
        self,
        provider_id: str,
        old_state: str,
        new_state: str,
        actor: str = "system",
        correlation_id: str | None = None,
    ) -> None:
        """Record provider state transition.

        Args:
            provider_id: Provider identifier
            old_state: Previous state
            new_state: New state
            actor: Who triggered the change
            correlation_id: Optional correlation ID
        """
        await self._repo.append(
            AuditEntry(
                entity_id=provider_id,
                entity_type="provider",
                action=AuditAction.STATE_CHANGED,
                timestamp=datetime.utcnow(),
                actor=actor,
                old_state={"state": old_state},
                new_state={"state": new_state},
                correlation_id=correlation_id,
            )
        )

    async def record_tool_invocation(
        self,
        provider_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        duration_ms: float,
        success: bool,
        error: str | None = None,
        actor: str = "user",
        correlation_id: str | None = None,
    ) -> None:
        """Record tool invocation for accountability.

        Args:
            provider_id: Provider identifier
            tool_name: Tool that was invoked
            arguments: Arguments passed to tool
            result: Tool result (sanitized)
            duration_ms: Invocation duration
            success: Whether invocation succeeded
            error: Error message if failed
            actor: Who invoked the tool
            correlation_id: Correlation ID from request
        """
        # Note: Consider sanitizing arguments/results for sensitive data
        await self._repo.append(
            AuditEntry(
                entity_id=f"{provider_id}:{tool_name}",
                entity_type="tool_invocation",
                action=AuditAction.UPDATED if success else AuditAction.STATE_CHANGED,
                timestamp=datetime.utcnow(),
                actor=actor,
                metadata={
                    "provider_id": provider_id,
                    "tool_name": tool_name,
                    "arguments_keys": list(arguments.keys()),  # Only log keys, not values
                    "duration_ms": duration_ms,
                    "success": success,
                    "error": error,
                    "result_type": type(result).__name__ if result else None,
                },
                correlation_id=correlation_id,
            )
        )

    async def record_from_event(
        self,
        event: DomainEvent,
        actor: str = "system",
    ) -> None:
        """Create audit entry from domain event.

        Maps domain events to appropriate audit entries.

        Args:
            event: Domain event to record
            actor: Actor associated with the event
        """
        correlation_id = getattr(event, "correlation_id", None)

        if isinstance(event, ProviderStarted):
            await self.record_provider_started(
                provider_id=event.provider_id,
                mode=event.mode,
                tools_count=event.tools_count,
                startup_duration_ms=event.startup_duration_ms,
                actor=actor,
                correlation_id=correlation_id,
            )

        elif isinstance(event, ProviderStopped):
            await self.record_provider_stopped(
                provider_id=event.provider_id,
                reason=event.reason,
                actor=actor,
                correlation_id=correlation_id,
            )

        elif isinstance(event, ProviderDegraded):
            await self.record_provider_degraded(
                provider_id=event.provider_id,
                consecutive_failures=event.consecutive_failures,
                total_failures=event.total_failures,
                reason=event.reason,
                actor=actor,
                correlation_id=correlation_id,
            )

        elif isinstance(event, ProviderStateChanged):
            await self.record_state_change(
                provider_id=event.provider_id,
                old_state=event.old_state,
                new_state=event.new_state,
                actor=actor,
                correlation_id=correlation_id,
            )

        elif isinstance(event, ToolInvocationCompleted):
            await self.record_tool_invocation(
                provider_id=event.provider_id,
                tool_name=event.tool_name,
                arguments={},  # Not available in event
                result=None,
                duration_ms=event.duration_ms,
                success=True,
                actor=actor,
                correlation_id=event.correlation_id,
            )

        elif isinstance(event, ToolInvocationFailed):
            await self.record_tool_invocation(
                provider_id=event.provider_id,
                tool_name=event.tool_name,
                arguments={},
                result=None,
                duration_ms=event.duration_ms,
                success=False,
                error=event.error,
                actor=actor,
                correlation_id=event.correlation_id,
            )
