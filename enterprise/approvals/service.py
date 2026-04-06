"""ApprovalGateService -- orchestrator for the approval gate flow.

Called from mcp_tool_wrapper's check_approval hook. Coordinates:
  1. Policy check (requires_approval?)
  2. Request creation and persistence
  3. Domain event publishing
  4. Hold registration and waiting
  5. Delivery notification dispatch
  6. State update on resolution/timeout
"""

import asyncio
import concurrent.futures
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp_hangar.domain.events import (
    ToolApprovalDenied,
    ToolApprovalExpired,
    ToolApprovalGranted,
    ToolApprovalRequested,
)
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.logging_config import get_logger
from mcp_hangar.observability.tracing import get_tracer

from .delivery.base import ApprovalDelivery
from .hold_registry import ApprovalHoldRegistry
from .models import ApprovalRequest, ApprovalResult, ApprovalState
from .persistence.sqlite_approval_repository import ApprovalRepository

logger = get_logger(__name__)

# Dedicated thread pool for _publish() to avoid deadlock with the default
# executor.  The batch executor's worker threads block on future.result() via
# run_coroutine_threadsafe; if _publish() used asyncio.to_thread (default
# executor), it would compete for the same threads -> circular wait.
_publish_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="approval-publish"
)


def _sanitize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive keys from arguments before persist/delivery."""
    sensitive_patterns = {"password", "token", "secret", "key", "auth", "credential"}
    sanitized = {}
    for k, v in arguments.items():
        if any(pattern in k.lower() for pattern in sensitive_patterns):
            sanitized[k] = "[REDACTED]"
        else:
            sanitized[k] = v
    return sanitized


def _hash_arguments(arguments: dict[str, Any]) -> str:
    """SHA-256 hash of sanitized arguments for integrity checking."""
    serialized = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


class ApprovalGateService:
    """Orchestrates the full approval gate flow."""

    def __init__(
        self,
        repository: ApprovalRepository,
        hold_registry: ApprovalHoldRegistry,
        event_bus: Any,
        delivery: ApprovalDelivery,
    ) -> None:
        self._repository = repository
        self._hold_registry = hold_registry
        self._event_bus = event_bus
        self._delivery = delivery

    async def check(
        self,
        provider_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        policy: ToolAccessPolicy,
        correlation_id: str,
    ) -> ApprovalResult:
        """Called from mcp_tool_wrapper check_approval hook.

        1. Check if tool requires approval
        2. Sanitize arguments, create request, persist
        3. Publish ToolApprovalRequested event
        4. Register hold, deliver notification
        5. Wait for resolution or timeout
        6. Publish outcome event, return result
        """
        if not policy.requires_approval(tool_name):
            return ApprovalResult.not_required()

        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("approval_gate.flow") as gate_span:
            gate_span.set_attribute("mcp.provider.id", provider_id)
            gate_span.set_attribute("mcp.tool.name", tool_name)
            gate_span.set_attribute("approval.channel", policy.approval_channel)
            gate_span.set_attribute("approval.timeout_seconds", policy.approval_timeout_seconds)

            approval_id = str(uuid.uuid4())
            gate_span.set_attribute("approval.id", approval_id)
            now = datetime.now(timezone.utc)
            sanitized_args = _sanitize_arguments(arguments)
            args_hash = _hash_arguments(sanitized_args)
            expires_at = now + timedelta(seconds=policy.approval_timeout_seconds)

            request = ApprovalRequest(
                approval_id=approval_id,
                provider_id=provider_id,
                tool_name=tool_name,
                arguments=sanitized_args,
                arguments_hash=args_hash,
                requested_at=now,
                expires_at=expires_at,
                state=ApprovalState.PENDING,
                channel=policy.approval_channel,
                correlation_id=correlation_id,
            )

            await self._repository.save(request)

            requested_event = ToolApprovalRequested(
                approval_id=approval_id,
                provider_id=provider_id,
                tool_name=tool_name,
                arguments_hash=args_hash,
                channel=policy.approval_channel,
                expires_at=expires_at.isoformat(),
                correlation_id=correlation_id,
            )
            await self._publish(requested_event)

            await self._hold_registry.register(approval_id)

            try:
                await self._delivery.send(request)
            except Exception:
                logger.warning(
                    "approval_delivery_failed",
                    approval_id=approval_id,
                    exc_info=True,
                )

            with tracer.start_as_current_span("approval_gate.wait_for_decision") as wait_span:
                wait_span.set_attribute("approval.id", approval_id)
                wait_span.set_attribute("approval.timeout_seconds", policy.approval_timeout_seconds)
                decision = await self._hold_registry.wait(
                    approval_id, policy.approval_timeout_seconds
                )
                if decision is True:
                    wait_span.set_attribute("approval.decision", "approved")
                elif decision is False:
                    wait_span.set_attribute("approval.decision", "denied")
                else:
                    wait_span.set_attribute("approval.decision", "expired")

            if decision is True:
                # State already updated by resolve() -- just reload for event data
                updated = await self._repository.get(approval_id)
                decided_by = updated.decided_by if updated else "unknown"
                decided_at = updated.decided_at if updated else datetime.now(timezone.utc)

                await self._publish(
                    ToolApprovalGranted(
                        approval_id=approval_id,
                        provider_id=provider_id,
                        tool_name=tool_name,
                        decided_by=decided_by,
                        decided_at=decided_at.isoformat(),
                    )
                )
                gate_span.set_attribute("approval.result", "granted")
                return ApprovalResult.granted(approval_id)

            if decision is False:
                # State already updated by resolve() -- just reload for event data
                updated = await self._repository.get(approval_id)
                decided_by = updated.decided_by if updated else "unknown"
                decided_at = updated.decided_at if updated else datetime.now(timezone.utc)
                reason = updated.reason if updated else None

                await self._publish(
                    ToolApprovalDenied(
                        approval_id=approval_id,
                        provider_id=provider_id,
                        tool_name=tool_name,
                        decided_by=decided_by,
                        decided_at=decided_at.isoformat(),
                        reason=reason,
                    )
                )
                gate_span.set_attribute("approval.result", "denied")
                return ApprovalResult.denied(approval_id, reason)

            # Timeout
            expired_at = datetime.now(timezone.utc)
            await self._repository.update_state(
                approval_id, ApprovalState.EXPIRED, None, expired_at, None
            )

            await self._publish(
                ToolApprovalExpired(
                    approval_id=approval_id,
                    provider_id=provider_id,
                    tool_name=tool_name,
                    expired_at=expired_at.isoformat(),
                )
            )
            gate_span.set_attribute("approval.result", "expired")
            return ApprovalResult.expired(approval_id)

    async def resolve(
        self,
        approval_id: str,
        approved: bool,
        decided_by: str,
        reason: str | None = None,
    ) -> bool:
        """Called from REST endpoint. Returns False if approval not found or already terminal."""
        request = await self._repository.get(approval_id)
        if request is None or request.is_terminal():
            return False

        # Store decided_by/reason before resolving the hold so check() can read them
        decided_at = datetime.now(timezone.utc)
        state = ApprovalState.APPROVED if approved else ApprovalState.DENIED
        await self._repository.update_state(
            approval_id, state, decided_by, decided_at, reason
        )

        return await self._hold_registry.resolve(approval_id, approved)

    async def _publish(self, event: Any) -> None:
        """Publish a domain event via the event bus without blocking the event loop.

        Uses a dedicated thread pool to avoid deadlock with the default executor
        that FastMCP and batch worker threads share.
        """
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(_publish_executor, self._event_bus.publish, event)
        except Exception:
            logger.warning("approval_event_publish_failed", exc_info=True)
