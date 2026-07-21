"""V2-native governance ledger for relayed MCP ``tasks/*`` (ADR-014).

Hangar does not run tasks and never holds their results. Under the
relay-with-governance model (ADR-014) an upstream MCP server owns task
execution; Hangar sits in front and keeps a synchronous, in-memory
**governance ledger** recording, per relayed task, only:

* the owning identity (tenant + principal) bound at relay time;
* the tool digest pinned on the synchronous invoke path (supply-chain pin);
* an upstream-truth :class:`~mcp_types.Task` *snapshot* (status + timestamps
  copied verbatim from the upstream), plus local relay provenance.

It holds governance metadata ONLY -- never a result payload and never any
execution state. Task ids are unique only per upstream, so every entry is keyed
on the composite :data:`TaskKey = (target_server_id, task_id)`; two upstreams may
legitimately mint the same ``task_id``.

Authorization is fail-closed and runs on every public path through the single
:meth:`authorize` chokepoint (which delegates to
:class:`~mcp_hangar.domain.services.task_ownership.TaskOwnershipRegistry`):

* Reads (:meth:`get_task`, :meth:`list_tasks`) return ``None`` / exclude the
  entry on denial -- a denied caller cannot distinguish "not found" from "not
  yours", so task existence never leaks.
* Mutations (:meth:`update_snapshot`, :meth:`delete_task`) raise
  :class:`McpError` with ``INVALID_PARAMS`` and the same ``"Task not found:
  <id>"`` message, so denial does not confirm existence.

Anonymous / system path: when no identity is bound, the caller is
``TaskOwner(None, None)`` -- it can only reach unattributed entries and can NEVER
reach a task owned by an attributed tenant/principal.

Supply-chain integrity across the async boundary: a task relayed under a pinned
tool digest is re-verified fail-closed via :meth:`_verify_pinned_digest`. On
drift -- or an unverifiable current schema -- the task is failed
(:meth:`fail_task`), a :class:`DigestMismatchInTask` provenance event is emitted,
and an ``McpError`` is raised (this closes the ADR-008 zombie: a task can never
complete against a tool contract the caller did not authorize).

Eviction safety: the ownership registry and digest guard are TTL/LRU bounded.
When either evicts a still-live binding, its ``on_evict`` callback fails the
ledger entry closed (``TaskFailed('evicted')``) rather than letting it silently
vanish.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import threading
from typing import Any

from mcp_hangar._sdk_compat import (
    INVALID_PARAMS,
    Task,
    TaskStatus,
    make_mcp_error,
)
from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.application.tasks.tool_pin_context import get_current_tool_pin
from mcp_hangar.context import get_identity_context
from mcp_hangar.domain.events import DigestMismatchInTask, TaskFailed
from mcp_hangar.domain.services.digest_computation import compute_tool_digest
from mcp_hangar.domain.services.task_digest_guard import TaskDigestGuard
from mcp_hangar.domain.services.task_ownership import TaskKey, TaskOwner, TaskOwnershipRegistry
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

# Terminal task states: an entry in one of these is never re-failed on eviction.
_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})


@dataclass
class TaskEntry:
    """A single governance-ledger record for one relayed task.

    Holds governance metadata ONLY -- no result payload, no execution state.

    Attributes:
        snapshot: Upstream-truth :class:`Task` (status/timestamps copied verbatim
            from the upstream at relay time; never Hangar-synthesized).
        owner: Identity (tenant + principal) bound at relay time.
        target_server_id: Upstream server id this task lives on (the first half
            of the composite key).
        correlation_id: Request correlation id linking the ledger entry to the
            provenance chain (populated at the P3 relay seam).
        original_result_type: Marker (class name / tag) for a future
            ``tasks/result`` reconstruction; empty when unknown.
        relayed_at: LOCAL relay ISO-8601 timestamp. This is Hangar's own clock
            and is NEVER surfaced as the upstream ``created_at``.
    """

    snapshot: Task
    owner: TaskOwner
    target_server_id: str
    correlation_id: str = ""
    original_result_type: str = ""
    relayed_at: str = ""


class GovernedTaskStore:
    """Synchronous in-memory governance ledger for relayed MCP tasks (ADR-014).

    Not a task store: it runs nothing and stores no results. It binds each
    relayed task to its owner and pinned tool digest, keeps an upstream-truth
    snapshot, and fail-closed-authorizes every access. See the module docstring
    for the denial policy, digest re-verification, and eviction safety.
    """

    def __init__(
        self,
        registry: TaskOwnershipRegistry | None = None,
        digest_guard: TaskDigestGuard | None = None,
        event_publisher: Callable[[object], None] | None = None,
    ) -> None:
        self._event_publisher = event_publisher
        # Re-entrant: register_relayed_task holds this lock while the primitives'
        # on_evict may fire synchronously and re-enter via fail_task (same thread).
        self._tasks_lock = threading.RLock()
        self._tasks: dict[TaskKey, TaskEntry] = {}
        # key -> (mcp_server, tool_name, pinned_digest) for tasks relayed under a
        # pinned digest; a key absent here was relayed without a pin (no re-verify).
        self._task_tools: dict[TaskKey, tuple[str, str, str]] = {}
        self._task_tools_lock = threading.Lock()
        # Default-construct the primitives wired to fail-close an evicted entry.
        self._registry: TaskOwnershipRegistry = (
            registry if registry is not None else TaskOwnershipRegistry(on_evict=self._on_evict)
        )
        self._digest_guard: TaskDigestGuard = (
            digest_guard if digest_guard is not None else TaskDigestGuard(on_evict=self._on_evict)
        )

    @property
    def registry(self) -> TaskOwnershipRegistry:
        """The ownership registry backing this ledger (for shared wiring/tests)."""
        return self._registry

    @property
    def digest_guard(self) -> TaskDigestGuard:
        """The digest guard backing this ledger (for shared wiring/tests)."""
        return self._digest_guard

    # -- identity / authorization chokepoint ---------------------------------

    def _current_caller(self) -> TaskOwner:
        """Build the :class:`TaskOwner` for the current request from context.

        ``tenant_id`` comes from the bound identity's caller; the principal is the
        caller's ``user_id`` if present, otherwise its ``agent_id``. When no
        identity is bound, both dimensions are ``None`` (unattributed caller).
        """
        identity = get_identity_context()
        if identity is None or identity.caller is None:
            return TaskOwner(tenant_id=None, principal_id=None)
        caller = identity.caller
        return TaskOwner(tenant_id=caller.tenant_id, principal_id=caller.user_id or caller.agent_id)

    def authorize(self, key: TaskKey) -> bool:
        """Fail-closed: does the current caller own ``key``? THE single chokepoint.

        Every public read/write path calls this FIRST before touching the ledger.
        """
        caller = self._current_caller()
        allowed = self._registry.authorize(key, caller)
        if not allowed:
            logger.warning(
                "governed_task_access_denied",
                target_server_id=key[0],
                task_id=key[1],
                tenant_id=caller.tenant_id,
            )
        return allowed

    # -- relay registration --------------------------------------------------

    def register_relayed_task(
        self,
        *,
        target_server_id: str,
        task: Task,
        expected_owner: TaskOwner,
    ) -> TaskOwner:
        """Record a relayed task in the ledger, binding owner + pinned digest.

        The owner is derived from the current request identity; it MUST agree
        (on tenant) with ``expected_owner`` computed by the caller, otherwise the
        contextvar has diverged from the authorized identity and we fail closed
        rather than orphan the binding. A cross-owner rebind of a live key raises
        :class:`~mcp_hangar.domain.services.task_ownership.TaskOwnerConflictError`.

        This is pure synchronous dict work -- no event loop, no I/O. ``TaskCreated``
        is emitted at the P3 relay seam, not here.
        """
        owner = self._current_caller()
        if owner.tenant_id != expected_owner.tenant_id:
            raise ValueError(
                "relay identity diverged: current caller tenant "
                f"{owner.tenant_id!r} != expected {expected_owner.tenant_id!r}"
            )
        relayed_at = datetime.now(UTC).isoformat()
        with self._tasks_lock:
            key: TaskKey = (target_server_id, task.task_id)
            self._registry.register(key, owner)  # propagates TaskOwnerConflictError
            pin = get_current_tool_pin()
            if pin is not None:
                self._digest_guard.pin(key, pin.pinned_digest)
                with self._task_tools_lock:
                    self._task_tools[key] = (pin.mcp_server, pin.tool_name, pin.pinned_digest)
            self._tasks[key] = TaskEntry(
                snapshot=task,
                owner=owner,
                target_server_id=target_server_id,
                correlation_id="",
                original_result_type="",
                relayed_at=relayed_at,
            )
        logger.debug(
            "governed_task_relayed",
            target_server_id=target_server_id,
            task_id=task.task_id,
            tenant_id=owner.tenant_id,
            has_principal=owner.principal_id is not None,
            digest_pinned=get_current_tool_pin() is not None,
        )
        return owner

    def mint_from_upstream(self, upstream: dict[str, Any]) -> Task:
        """Build a v2 :class:`Task` from an upstream task dict, verbatim + fail-closed.

        The canonical task id is resolved by precedence ``taskId`` > ``task_id`` >
        ``id`` (``ValueError`` if none present). Status, status message, timestamps,
        ttl and poll interval are copied VERBATIM -- never synthesized. If any field
        the v2 ``Task`` model requires (``status``, ``created_at``,
        ``last_updated_at``, ``ttl``) is genuinely absent, the upstream is treated as
        MALFORMED and ``ValueError`` is raised (fail closed).
        """
        task_id = upstream.get("taskId") or upstream.get("task_id") or upstream.get("id")
        if not task_id:
            raise ValueError("upstream task is missing a task id (taskId/task_id/id)")
        data = dict(upstream)
        data["taskId"] = task_id
        # Task's model_config accepts either alias (camelCase) or field name and
        # preserves values verbatim; a missing required field raises ValidationError
        # (a ValueError subclass) -> fail closed, no synthesized default.
        return Task.model_validate(data)

    # -- authorized reads ----------------------------------------------------

    def get_task(self, key: TaskKey) -> Task | None:
        """Return the task snapshot only if the caller owns ``key``, else ``None``.

        Denial is indistinguishable from "not found": no existence leak.
        """
        if not self.authorize(key):
            return None
        with self._tasks_lock:
            entry = self._tasks.get(key)
            return entry.snapshot if entry is not None else None

    def list_tasks(self, caller: TaskOwner | None = None) -> tuple[list[Task], str | None]:
        """Return the caller's owned snapshots in ONE page (``next_cursor=None``).

        An inner/upstream cursor is never forwarded -- it could identify another
        tenant's task -- so the whole owned set is returned as a single page.
        """
        if caller is None:
            caller = self._current_caller()
        with self._tasks_lock:
            items = list(self._tasks.items())
        own = [entry.snapshot for key, entry in items if self._registry.authorize(key, caller)]
        return own, None

    # -- authorized mutations ------------------------------------------------

    def update_snapshot(
        self,
        key: TaskKey,
        status: TaskStatus | None = None,
        status_message: str | None = None,
    ) -> None:
        """Update the snapshot's status only if the caller owns ``key``, else fail closed."""
        if not self.authorize(key):
            raise make_mcp_error(INVALID_PARAMS, f"Task not found: {key[1]}")
        with self._tasks_lock:
            entry = self._tasks.get(key)
            if entry is None:
                raise make_mcp_error(INVALID_PARAMS, f"Task not found: {key[1]}")
            entry.snapshot = self._with_status(entry.snapshot, status, status_message)

    def delete_task(self, key: TaskKey) -> None:
        """Delete the ledger entry only if the caller owns ``key``, else fail closed."""
        if not self.authorize(key):
            raise make_mcp_error(INVALID_PARAMS, f"Task not found: {key[1]}")
        with self._tasks_lock:
            self._tasks.pop(key, None)
        self._registry.discard(key)
        self._digest_guard.discard(key)
        with self._task_tools_lock:
            self._task_tools.pop(key, None)

    # -- fail-closed lifecycle ----------------------------------------------

    def fail_task(self, key: TaskKey, error_type: str, error_message: str = "") -> None:
        """Mark the ledger entry failed and publish ``TaskFailed`` (idempotent-safe).

        Also the target of the primitives' ``on_evict``: a still-live evicted entry
        is failed closed here. Does not call back into the registry/guard mutation
        paths, so it cannot re-trigger eviction (no recursion).
        """
        with self._tasks_lock:
            entry = self._tasks.get(key)
            if entry is None:
                return
            entry.snapshot = self._with_status(entry.snapshot, "failed", error_message or None)
            owner = entry.owner
            correlation_id = entry.correlation_id
        if self._event_publisher is not None:
            self._publish(
                TaskFailed(
                    task_id=key[1],
                    tenant_id=owner.tenant_id,
                    correlation_id=correlation_id,
                    error_type=error_type,
                    error_message=error_message,
                )
            )

    def _on_evict(self, key: TaskKey) -> None:
        """Primitive-eviction callback: fail a still-live entry closed, then purge.

        Fires OUTSIDE the evicting primitive's lock, so discarding from the other
        primitive here is safe. Terminal entries are only purged (no re-fail).
        """
        with self._tasks_lock:
            entry = self._tasks.get(key)
            non_terminal = entry is not None and entry.snapshot.status not in _TERMINAL_STATUSES
        if non_terminal:
            self.fail_task(key, "evicted")
        # Symmetric cleanup: whichever primitive evicted, drop the mirror binding
        # and the ledger entry so nothing leaks (both discards are no-op safe).
        self._registry.discard(key)
        self._digest_guard.discard(key)
        with self._tasks_lock:
            self._tasks.pop(key, None)
        with self._task_tools_lock:
            self._task_tools.pop(key, None)

    # -- supply-chain digest re-verification ---------------------------------

    def _verify_pinned_digest(self, key: TaskKey) -> None:
        """Fail closed unless the tool's CURRENT digest matches the task's pin.

        No-op for a task relayed without a pinned digest. On drift -- or when the
        tool's current schema cannot be verified (observed ``None``) -- the task is
        failed (:meth:`fail_task`), a :class:`DigestMismatchInTask` provenance event
        is emitted, and an ``McpError`` (``INVALID_PARAMS``) is raised. This kills
        the ADR-008 zombie: a task never completes against a drifted tool contract.
        """
        with self._task_tools_lock:
            bound = self._task_tools.get(key)
        if bound is None:
            return  # Relayed without a pin: nothing to re-verify.
        mcp_server, tool_name, expected_digest = bound
        observed: str | None = None
        try:
            projection = get_tool_projection_registry().resolve(mcp_server, tool_name)
            if projection is not None:
                observed = compute_tool_digest(projection.schema).sha256
        except Exception:  # noqa: BLE001 -- any lookup/compute failure is a fail-closed verification failure
            observed = None
        if observed is None or not self._digest_guard.verify(key, observed):
            logger.warning(
                "governed_task_digest_drift",
                target_server_id=key[0],
                task_id=key[1],
                mcp_server=mcp_server,
                tool=tool_name,
                verifiable=observed is not None,
            )
            self._route_digest_drift(key, mcp_server, tool_name, expected_digest, observed)
            raise make_mcp_error(INVALID_PARAMS, "tool digest drifted since task creation")

    def _route_digest_drift(
        self,
        key: TaskKey,
        mcp_server: str,
        tool_name: str,
        expected_digest: str,
        observed: str | None,
    ) -> None:
        """Fail the task closed and emit the task-keyed digest-mismatch provenance event."""
        with self._tasks_lock:
            entry = self._tasks.get(key)
            owner = entry.owner if entry is not None else TaskOwner(None, None)
            correlation_id = entry.correlation_id if entry is not None else ""
        self.fail_task(key, "digest_drift")
        if self._event_publisher is not None:
            self._publish(
                DigestMismatchInTask(
                    task_id=key[1],
                    target_server_id=key[0],
                    tenant_id=owner.tenant_id,
                    correlation_id=correlation_id,
                    mcp_server_id=mcp_server,
                    tool_name=tool_name,
                    expected_digest=expected_digest,
                    observed_digest=observed,
                )
            )

    # -- helpers -------------------------------------------------------------

    def _with_status(
        self,
        task: Task,
        status: TaskStatus | None,
        status_message: str | None,
    ) -> Task:
        """Return a copy of ``task`` with status/status_message updated (timestamps verbatim)."""
        updates: dict[str, Any] = {}
        if status is not None:
            updates["status"] = status
        if status_message is not None:
            updates["status_message"] = status_message
        if not updates:
            return task
        return task.model_copy(update=updates)

    def _publish(self, event: object) -> None:
        """Best-effort event emission; a publisher failure never breaks the ledger."""
        publisher = self._event_publisher
        if publisher is None:
            return
        try:
            publisher(event)
        except Exception:  # noqa: BLE001 -- event publication must not break governance
            logger.warning("governed_task_event_publish_failed", event_type=type(event).__name__)


__all__ = ["GovernedTaskStore", "TaskEntry", "TaskKey"]
