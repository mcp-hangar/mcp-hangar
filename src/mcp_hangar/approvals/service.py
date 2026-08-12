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
import time
import uuid
from datetime import datetime, timedelta, UTC
from typing import Any

from mcp_hangar.domain.events import (
    ToolApprovalDenied,
    ToolApprovalExpired,
    ToolApprovalGranted,
    ToolApprovalRequested,
)
from mcp_hangar.metrics import (
    APPROVAL_DECISIONS_TOTAL,
    APPROVAL_DELIVERIES_TOTAL,
    APPROVAL_REQUESTS_TOTAL,
)
from mcp_hangar.redactor import get_default_redactor
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.logging_config import get_logger
from mcp_hangar.observability.tracing import get_tracer

from .delivery.base import ApprovalDelivery
from .hold_registry import ApprovalHoldRegistry
from .models import ApprovalRequest, ApprovalResult, ApprovalState
from .persistence.sqlite_approval_repository import ApprovalRepository

logger = get_logger(__name__)

#: How often the wait re-reads the approval record while holding a call.
#:
#: Only matters when the decision lands on a different instance than the call --
#: a local resolution signals immediately and never waits for this. Two seconds
#: is chosen against a gate whose timeout is measured in minutes: the added
#: latency is invisible to a human approver, and the read is one indexed row per
#: held call.
SHARED_POLL_INTERVAL_S = 2.0

# Dedicated thread pool for _publish() to avoid deadlock with the default
# executor.  The batch executor's worker threads block on future.result() via
# run_coroutine_threadsafe; if _publish() used asyncio.to_thread (default
# executor), it would compete for the same threads -> circular wait.
_publish_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="approval-publish")


def _sanitize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets from arguments before they are persisted or delivered.

    Two passes, because either alone leaks:

    1. by key name -- ``password``, ``token``, ``secret``, ``key``, ``auth``,
       ``credential`` as substrings;
    2. by value shape, using the shared builtin-pattern redactor (JWTs, Bearer
       headers, ``ghp_``/``AKIA``/``xox``-style keys, connection strings).

    Pass 1 alone was the whole of this function, so a secret under a
    non-matching key -- ``{"body": "Authorization: Bearer eyJ..."}`` or
    ``{"dsn": "postgres://user:pw@host"}`` -- was written verbatim into the
    SQLite approval record and served to every ``approval:read`` holder through
    the REST DTO. The value redactor already existed and is used by the log
    pipeline and the stderr capture; approvals just were not using it.

    Nested dicts and lists are walked, since MCP tool arguments are arbitrary
    JSON, with the same depth cap the log pipeline uses.
    """
    sensitive_patterns = {"password", "token", "secret", "key", "auth", "credential"}
    redactor = get_default_redactor()

    def scrub(value: Any, depth: int = 0) -> Any:
        if depth > 5:
            return value
        if isinstance(value, str):
            return redactor.redact(value)
        if isinstance(value, dict):
            return {k: scrub(v, depth + 1) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(item, depth + 1) for item in value]
        return value

    sanitized: dict[str, Any] = {}
    for key, value in arguments.items():
        if any(pattern in key.lower() for pattern in sensitive_patterns):
            sanitized[key] = "[REDACTED]"
        else:
            sanitized[key] = scrub(value)
    return sanitized


def _hash_arguments(arguments: dict[str, Any]) -> str:
    """SHA-256 over the RAW arguments, for the dispatch-time integrity check.

    Confidentiality and integrity are different jobs and this hash is the
    integrity one: it answers "is the payload about to be dispatched the payload
    the approver saw approved". Hashing the *redacted* copy instead -- which is
    what this did while redaction was key-name-only, and which would become
    actively unsafe now that values are redacted too -- makes the check blind to
    exactly the substitutions worth catching: two different tokens both redact
    to the same marker, hash identically, and swap freely between approval and
    dispatch.

    Upgrade note: approvals already pending when this ships were hashed over the
    old (sanitized) projection, so they will fail revalidation and be refused.
    That is the fail-closed direction -- a refused approval can be re-requested;
    a silently accepted substitution cannot be undone.
    """
    serialized = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


class ApprovalGateService:
    """Orchestrates the full approval gate flow."""

    async def revalidate(self, approval_id: str, arguments: dict[str, Any]) -> str | None:
        """Re-establish an approval's validity at dispatch time.

        The gate decides once, then blocks -- by default for up to five minutes,
        and longer if configured. Everything the decision rested on was checked
        *before* that pause and nothing was checked after it, so the call could
        be dispatched against a world the approver never saw.

        Returns a refusal reason, or ``None`` when the approval still holds.
        """
        request = await self._repository.get(approval_id)
        if request is None:
            return "approval record is gone"
        if request.state is not ApprovalState.APPROVED:
            return f"approval is {request.state.value}, not approved"
        if request.is_expired():
            return "approval expired during the hold"
        if _hash_arguments(arguments) != request.arguments_hash:
            # `arguments_hash` was computed, persisted, emitted and shown to the
            # approver, and compared against nothing -- its own docstring says
            # "for integrity checking". The request mutator pipeline runs after
            # the gate, so the dispatched payload can legitimately differ from
            # the approved one with nothing to notice.
            return "arguments changed after approval"
        return None

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
        tool_name: str,
        arguments: dict[str, Any],
        policy: ToolAccessPolicy,
        correlation_id: str,
        mcp_server_id: str | None = None,
        provider_id: str | None = None,
        tenant_id: str | None = None,
        requested_by: str | None = None,
    ) -> ApprovalResult:
        """Called from mcp_tool_wrapper check_approval hook.

        1. Check if tool requires approval
        2. Sanitize arguments, create request, persist
        3. Publish ToolApprovalRequested event
        4. Register hold, deliver notification
        5. Wait for resolution or timeout
        6. Publish outcome event, return result
        """
        resolved_provider_id = provider_id or mcp_server_id
        if resolved_provider_id is None:
            raise TypeError("Missing required argument: mcp_server_id")

        if not policy.requires_approval(tool_name):
            return ApprovalResult.not_required()

        # An approval records the channel that will actually carry it. The policy
        # may name none, which means the deployment's configured channel --
        # resolving it once here keeps the record, the event, the metric and the
        # span attribute all saying the same thing the router will do.
        channel = policy.approval_channel or getattr(self._delivery, "default_channel", "")

        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("approval_gate.flow") as gate_span:
            gate_span.set_attribute("mcp.server.id", resolved_provider_id)
            gate_span.set_attribute("gen_ai.tool.name", tool_name)
            gate_span.set_attribute("approval.channel", channel)
            gate_span.set_attribute("approval.timeout_seconds", policy.approval_timeout_seconds)

            approval_id = str(uuid.uuid4())
            gate_span.set_attribute("approval.id", approval_id)
            now = datetime.now(UTC)
            sanitized_args = _sanitize_arguments(arguments)
            args_hash = _hash_arguments(arguments)
            expires_at = now + timedelta(seconds=policy.approval_timeout_seconds)

            request = ApprovalRequest(
                approval_id=approval_id,
                provider_id=resolved_provider_id,
                tool_name=tool_name,
                arguments=sanitized_args,
                arguments_hash=args_hash,
                requested_at=now,
                expires_at=expires_at,
                state=ApprovalState.PENDING,
                channel=channel,
                correlation_id=correlation_id,
                tenant_id=tenant_id,
                requested_by=requested_by,
            )

            await self._repository.save(request)
            APPROVAL_REQUESTS_TOTAL.inc(channel=channel)

            requested_event = ToolApprovalRequested(
                approval_id=approval_id,
                mcp_server_id=resolved_provider_id,
                tool_name=tool_name,
                arguments_hash=args_hash,
                channel=channel,
                expires_at=expires_at.isoformat(),
                correlation_id=correlation_id,
            )
            await self._publish(requested_event)

            await self._hold_registry.register(approval_id)

            try:
                await self._delivery.send(request)
            except Exception:  # noqa: BLE001
                APPROVAL_DELIVERIES_TOTAL.inc(channel=channel, outcome="failed")
                logger.warning(
                    "approval_delivery_failed",
                    approval_id=approval_id,
                    exc_info=True,
                )

            with tracer.start_as_current_span("approval_gate.wait_for_decision") as wait_span:
                wait_span.set_attribute("approval.id", approval_id)
                wait_span.set_attribute("approval.timeout_seconds", policy.approval_timeout_seconds)
                decision = await self._wait_for_decision(approval_id, policy.approval_timeout_seconds)
                if decision is True:
                    wait_span.set_attribute("approval.decision", "approved")
                elif decision is False:
                    wait_span.set_attribute("approval.decision", "denied")
                else:
                    wait_span.set_attribute("approval.decision", "expired")

            if decision is True:
                # State already updated by resolve() -- just reload for event data
                updated = await self._repository.get(approval_id)
                decided_by = updated.decided_by if updated and updated.decided_by is not None else "unknown"
                decided_at = updated.decided_at if updated and updated.decided_at is not None else datetime.now(UTC)

                await self._publish(
                    ToolApprovalGranted(
                        approval_id=approval_id,
                        mcp_server_id=resolved_provider_id,
                        tool_name=tool_name,
                        decided_by=decided_by,
                        decided_at=decided_at.isoformat(),
                    )
                )
                gate_span.set_attribute("approval.result", "granted")
                APPROVAL_DECISIONS_TOTAL.inc(channel=channel, decision="granted")
                return ApprovalResult.granted(approval_id)

            if decision is False:
                # State already updated by resolve() -- just reload for event data
                updated = await self._repository.get(approval_id)
                decided_by = updated.decided_by if updated and updated.decided_by is not None else "unknown"
                decided_at = updated.decided_at if updated and updated.decided_at is not None else datetime.now(UTC)
                reason = updated.reason if updated else None

                await self._publish(
                    ToolApprovalDenied(
                        approval_id=approval_id,
                        mcp_server_id=resolved_provider_id,
                        tool_name=tool_name,
                        decided_by=decided_by,
                        decided_at=decided_at.isoformat(),
                        reason=reason,
                    )
                )
                gate_span.set_attribute("approval.result", "denied")
                APPROVAL_DECISIONS_TOTAL.inc(channel=channel, decision="denied")
                return ApprovalResult.denied(approval_id, reason)

            # Timeout
            expired_at = datetime.now(UTC)
            await self._repository.update_state(approval_id, ApprovalState.EXPIRED, None, expired_at, None)

            await self._publish(
                ToolApprovalExpired(
                    approval_id=approval_id,
                    mcp_server_id=resolved_provider_id,
                    tool_name=tool_name,
                    expired_at=expired_at.isoformat(),
                )
            )
            gate_span.set_attribute("approval.result", "expired")
            APPROVAL_DECISIONS_TOTAL.inc(channel=channel, decision="expired")
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
        decided_at = datetime.now(UTC)
        state = ApprovalState.APPROVED if approved else ApprovalState.DENIED
        await self._repository.update_state(approval_id, state, decided_by, decided_at, reason)

        return await self._hold_registry.resolve(approval_id, approved)

    async def _wait_for_decision(self, approval_id: str, timeout_seconds: int) -> bool | None:
        """Wait for a decision from either instance that could make one.

        Two sources, because there are two places a decision can appear:

        * the local hold, signalled the instant a resolution lands on **this**
          instance -- the common case, and the fast one;
        * the approval record, which is where a decision made on **another**
          instance shows up. With a shared storage backend that is the only
          thing the two instances have in common.

        Before this, the wait watched the local hold alone. A call held on A
        while the approver's request landed on B would sit until it timed out
        and then fail closed -- so the approver saw success, the caller saw a
        denial, and the record said approved. The record and the outcome
        disagreeing is worse than plain unavailability, and it was silent
        (#778).

        Args:
            approval_id: The held approval.
            timeout_seconds: How long the policy allows.

        Returns:
            True if approved, False if denied, None if the wait elapsed.
        """
        deadline = time.monotonic() + float(timeout_seconds)
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None

                decision = await self._hold_registry.wait_slice(approval_id, min(SHARED_POLL_INTERVAL_S, remaining))
                if decision is not None:
                    return decision

                decided = await self._decision_from_record(approval_id)
                if decided is not None:
                    logger.info("approval_decision_observed_from_storage", approval_id=approval_id)
                    return decided
        finally:
            self._hold_registry.release(approval_id)

    async def _decision_from_record(self, approval_id: str) -> bool | None:
        """A decision already written to the approval record, if there is one.

        Failures are swallowed: a storage hiccup must not turn a pending
        approval into a refusal. The wait continues and the deadline still
        applies, so the worst case is the behaviour that existed before.
        """
        try:
            record = await self._repository.get(approval_id)
        except Exception as e:  # noqa: BLE001 -- fault-barrier: a read failure must not decide the call
            logger.warning("approval_record_read_failed", approval_id=approval_id, error=str(e))
            return None

        if record is None:
            return None
        if record.state == ApprovalState.APPROVED:
            return True
        if record.state == ApprovalState.DENIED:
            return False
        return None

    async def _publish(self, event: Any) -> None:
        """Publish a domain event via the event bus without blocking the event loop.

        Uses a dedicated thread pool to avoid deadlock with the default executor
        that FastMCP and batch worker threads share.
        """
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(_publish_executor, self._event_bus.publish, event)
        except Exception:  # noqa: BLE001
            logger.warning("approval_event_publish_failed", exc_info=True)
