# pyright: reportExplicitAny=false

"""Async task lifecycle (SEP-2663 tasks/*)."""

from dataclasses import dataclass

from .base import DomainEvent


# Task Lifecycle Events
#
# One logical async task (e.g. an MCP/A2A tasks/* action) spans many round
# trips, unlike the single synchronous invoke captured by the tool-invocation
# events above. These events capture the full async lifecycle so the audit
# trail reflects the whole action, keyed on task_id. Every event carries
# tenant_id + task_id + correlation_id so the trail is reconstructable per
# task_id and attributable per tenant.


@dataclass
class TaskCreated(DomainEvent):
    """Published when an async task is created."""

    task_id: str
    tenant_id: str | None = None
    correlation_id: str = ""
    mcp_server_id: str | None = None
    tool_name: str = ""


@dataclass
class TaskInputRequired(DomainEvent):
    """Published when an in-flight task pauses awaiting caller input."""

    task_id: str
    tenant_id: str | None = None
    correlation_id: str = ""
    message: str = ""


@dataclass
class TaskCompleted(DomainEvent):
    """Published when a task finishes successfully."""

    task_id: str
    tenant_id: str | None = None
    correlation_id: str = ""
    duration_ms: float = 0.0


@dataclass
class TaskFailed(DomainEvent):
    """Published when a task terminates with an error."""

    task_id: str
    tenant_id: str | None = None
    correlation_id: str = ""
    error_type: str = ""
    error_message: str = ""


@dataclass
class TaskCancelled(DomainEvent):
    """Published when a task is cancelled before completion."""

    task_id: str
    tenant_id: str | None = None
    correlation_id: str = ""
    reason: str = ""
    cancelled_by: str = ""


@dataclass
class TaskConsentDecided(DomainEvent):
    """Published when a mid-flight ``input_required`` consent is decided (ADR-014 Phase 4).

    On the 2025-11-25 protocol a relayed task that pauses at ``input_required`` is
    resolved synchronously: Hangar elicits the downstream client for consent and
    records the outcome here (``granted=True`` on accept, ``False`` on a
    decline/cancel/failure/fail-closed denial), keyed by the task plus its
    ``target_server_id`` (task_ids are unique only per-upstream) and the
    deterministic ``input_key`` of the pending input. This joins the task's
    append-only provenance chain via its ``correlation_id`` + owner ``tenant_id``.
    """

    task_id: str
    target_server_id: str = ""
    input_key: str = ""
    granted: bool = False
    tenant_id: str | None = None
    correlation_id: str = ""
    principal_id: str = ""


@dataclass
class DigestMismatchInTask(DomainEvent):
    """Published when a relayed task's pinned tool digest drifts at result time.

    The task-keyed counterpart of :class:`DigestMismatchEvent` (ADR-014 relay-with-
    governance / #320): when ``get_result`` re-verifies a task's pinned tool digest
    and finds drift (or an unverifiable schema), the task is failed fail-closed and
    this event is emitted onto the append-only provenance chain, keyed by the task
    plus its ``target_server_id`` (task_ids are unique only per-upstream).
    """

    task_id: str
    target_server_id: str = ""
    tenant_id: str | None = None
    correlation_id: str = ""
    mcp_server_id: str | None = None
    tool_name: str = ""
    expected_digest: str = ""
    observed_digest: str | None = None
