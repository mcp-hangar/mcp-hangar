"""Ownership-governed :class:`TaskStore` for MCP ``tasks/*`` authorization.

The MCP experimental task API (``mcp.server.experimental``) drives task
lifecycle through a pluggable :class:`~mcp.shared.experimental.tasks.store.TaskStore`.
Task handles cannot be scoped to a transport session, so the server MUST keep
its own ``task_id -> owner`` map and authorize every access fail-closed;
otherwise a caller could guess or replay another tenant's handle and read or
cancel their task (see :mod:`mcp_hangar.domain.services.task_ownership`).

:class:`GovernedTaskStore` wraps an inner ``TaskStore`` and a
:class:`TaskOwnershipRegistry`. It:

* binds each newly created task to the CURRENT request identity
  (tenant + principal) read from ``identity_context_var``;
* authorizes ``get_task`` / ``get_result`` / ``update_task`` / ``delete_task``
  against that binding, fail-closed;
* filters ``list_tasks`` so a caller never sees another tenant's tasks.

WARNING: this is built on the EXPERIMENTAL mcp task API (mcp 1.26.0,
``mcp.server.experimental``); the wire format and store interface may change
without notice.

Fail-closed denial policy (documented, consistent):

* Read operations (``get_task``, ``get_result``) return ``None`` on denial —
  a denied caller cannot distinguish "not found" from "not yours", which
  avoids leaking task existence. This mirrors the default mcp handlers, which
  map a cross-session task to "not found".
* Mutating operations (``update_task``, ``delete_task``) raise
  :class:`~mcp.shared.exceptions.McpError` with ``INVALID_PARAMS`` and the same
  ``"Task not found: <id>"`` message the built-in handlers use, so denial does
  not confirm existence.

Anonymous / system path: when no identity is bound to the context, the caller
is treated as ``TaskOwner(None, None)``. ``create_task`` is still allowed
(the task is registered as unattributed), but authorization still runs — an
unattributed caller can only reach unattributed tasks and can NEVER reach a
task owned by an attributed tenant/principal. This keeps the store usable on
the system path while denying any cross-check that cannot be attributed.

Stages: Stage 1 is ownership bind + authorize. Stage 2 (#320) extends digest
pinning across the task lifecycle: ``create_task`` binds the task to the tool
digest pinned on the invoke path (read from the current-tool contextvar) and
``get_result`` re-verifies the tool's CURRENT digest fail-closed on drift.
Consent gating is a later stage (#322).
"""

from __future__ import annotations

import threading

from mcp.shared.exceptions import McpError
from mcp.shared.experimental.tasks.in_memory_task_store import InMemoryTaskStore
from mcp.shared.experimental.tasks.store import TaskStore
from mcp.types import INVALID_PARAMS, ErrorData, Result, Task, TaskMetadata, TaskStatus

from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.application.tasks.tool_pin_context import get_current_tool_pin
from mcp_hangar.context import get_identity_context
from mcp_hangar.domain.services.digest_computation import compute_tool_digest
from mcp_hangar.domain.services.task_digest_guard import TaskDigestGuard
from mcp_hangar.domain.services.task_ownership import TaskOwner, TaskOwnershipRegistry
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)


def _current_caller() -> TaskOwner:
    """Build the :class:`TaskOwner` for the current request from context.

    ``tenant_id`` comes from the bound identity's caller. The principal is the
    caller's ``user_id`` if present, otherwise its ``agent_id`` (a service /
    agent principal). When no identity is bound, both dimensions are ``None``
    (unattributed caller).
    """
    identity = get_identity_context()
    if identity is None or identity.caller is None:
        return TaskOwner(tenant_id=None, principal_id=None)
    caller = identity.caller
    principal_id = caller.user_id or caller.agent_id
    return TaskOwner(tenant_id=caller.tenant_id, principal_id=principal_id)


class GovernedTaskStore(TaskStore):
    """A :class:`TaskStore` that binds tasks to their owner and authorizes access.

    Wraps an inner ``TaskStore`` (default :class:`InMemoryTaskStore`) and a
    shared :class:`TaskOwnershipRegistry`. See the module docstring for the
    fail-closed denial policy and the anonymous/system-path handling.
    """

    def __init__(
        self,
        inner: TaskStore | None = None,
        registry: TaskOwnershipRegistry | None = None,
        digest_guard: TaskDigestGuard | None = None,
    ) -> None:
        self._inner: TaskStore = inner if inner is not None else InMemoryTaskStore()
        self._registry: TaskOwnershipRegistry = registry if registry is not None else TaskOwnershipRegistry()
        self._digest_guard: TaskDigestGuard = digest_guard if digest_guard is not None else TaskDigestGuard()
        # task_id -> (mcp_server, tool_name) for tasks bound to a pinned digest.
        # Records which tool a task's result must be re-verified against; a task
        # absent from this map was created without a pin (re-verify is skipped).
        # Guarded by a lock because create_task may run on a background task
        # while get_result runs on the retrieving request.
        self._task_tools: dict[str, tuple[str, str]] = {}
        self._task_tools_lock = threading.Lock()

    @property
    def registry(self) -> TaskOwnershipRegistry:
        """The ownership registry backing this store (for shared wiring/tests)."""
        return self._registry

    @property
    def digest_guard(self) -> TaskDigestGuard:
        """The digest guard backing this store (for shared wiring/tests)."""
        return self._digest_guard

    async def create_task(
        self,
        metadata: TaskMetadata,
        task_id: str | None = None,
    ) -> Task:
        """Create a task on the inner store, binding owner and pinned digest.

        Beyond the Stage 1 ownership binding, if the invoke path pinned a tool
        digest for this request (surfaced via the current-tool contextvar,
        #320), the task is bound to that digest so its result can be re-verified
        fail-closed on retrieval. The task's ``(mcp_server, tool)`` is recorded
        so the tool's current schema can be looked up at that later time.
        """
        task = await self._inner.create_task(metadata, task_id)
        owner = _current_caller()
        self._registry.register(task.taskId, owner)
        pin = get_current_tool_pin()
        if pin is not None:
            self._digest_guard.pin(task.taskId, pin.pinned_digest)
            with self._task_tools_lock:
                self._task_tools[task.taskId] = (pin.mcp_server, pin.tool_name)
        logger.debug(
            "governed_task_created",
            task_id=task.taskId,
            tenant_id=owner.tenant_id,
            has_principal=owner.principal_id is not None,
            digest_pinned=pin is not None,
        )
        return task

    async def get_task(self, task_id: str) -> Task | None:
        """Return the task only if the current caller owns it, else ``None``."""
        if not self._authorized(task_id):
            return None
        return await self._inner.get_task(task_id)

    async def get_result(self, task_id: str) -> Result | None:
        """Return the task result, re-verifying a pinned tool digest fail-closed.

        Ownership is checked first (Stage 1): a non-owner gets ``None`` and
        cannot distinguish "not found" from "not yours". If the caller owns the
        task and it was bound to a tool digest at creation (#320), the tool's
        CURRENT schema is re-digested and compared against the pinned digest;
        on drift -- or if the tool's current schema cannot be verified -- the
        result is withheld and an ``McpError`` is raised (fail-closed). A task
        created without a pin is unaffected.
        """
        if not self._authorized(task_id):
            return None
        self._verify_pinned_digest(task_id)
        return await self._inner.get_result(task_id)

    def _verify_pinned_digest(self, task_id: str) -> None:
        """Fail closed unless the tool's current digest matches the task's pin.

        No-op for a task created without a pinned digest. Raises ``McpError``
        (``INVALID_PARAMS``) when the tool's current schema drifted from -- or
        can no longer be verified against -- the digest pinned at creation.
        """
        with self._task_tools_lock:
            bound = self._task_tools.get(task_id)
        if bound is None:
            return  # Task was not created under a pin: nothing to re-verify.
        mcp_server, tool_name = bound
        observed: str | None = None
        try:
            projection = get_tool_projection_registry().resolve(mcp_server, tool_name)
            if projection is not None:
                observed = compute_tool_digest(projection.schema).sha256
        except Exception:  # noqa: BLE001 -- any lookup/compute failure is a fail-closed verification failure
            observed = None
        if observed is None or not self._digest_guard.verify(task_id, observed):
            logger.warning(
                "governed_task_digest_drift",
                task_id=task_id,
                mcp_server=mcp_server,
                tool=tool_name,
                verifiable=observed is not None,
            )
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="tool digest drifted since task creation",
                )
            )

    async def update_task(
        self,
        task_id: str,
        status: TaskStatus | None = None,
        status_message: str | None = None,
    ) -> Task:
        """Update the task only if the current caller owns it, else fail closed."""
        self._require_authorized(task_id)
        return await self._inner.update_task(task_id, status, status_message)

    async def delete_task(self, task_id: str) -> bool:
        """Delete the task only if the current caller owns it, else fail closed."""
        self._require_authorized(task_id)
        deleted = await self._inner.delete_task(task_id)
        if deleted:
            self._registry.discard(task_id)
            self._digest_guard.discard(task_id)
            with self._task_tools_lock:
                self._task_tools.pop(task_id, None)
        return deleted

    async def list_tasks(
        self,
        cursor: str | None = None,
    ) -> tuple[list[Task], str | None]:
        """List only the tasks the current caller is authorized for.

        The inner cursor is not forwarded to the caller: it is derived from the
        unfiltered listing and could identify another tenant's task. All of the
        caller's own tasks are returned in a single page (``next_cursor=None``).
        """
        caller = _current_caller()
        own: list[Task] = []
        inner_cursor: str | None = None
        while True:
            page, inner_cursor = await self._inner.list_tasks(inner_cursor)
            own.extend(task for task in page if self._registry.authorize(task.taskId, caller))
            if inner_cursor is None:
                return own, None

    async def store_result(self, task_id: str, result: Result) -> None:
        """Delegate to the inner store (internal lifecycle).

        Stage 2 (#320) re-verifies the pinned tool digest on RETRIEVAL
        (:meth:`get_result`), reflecting the tool's schema at the moment the
        caller reads the result. Storing the completed result is left to the
        inner store and is not gated here.
        """
        await self._inner.store_result(task_id, result)

    async def notify_update(self, task_id: str) -> None:
        """Delegate to the inner store (internal lifecycle signal)."""
        await self._inner.notify_update(task_id)

    async def wait_for_update(self, task_id: str) -> None:
        """Delegate to the inner store (internal lifecycle wait)."""
        await self._inner.wait_for_update(task_id)

    def _authorized(self, task_id: str) -> bool:
        caller = _current_caller()
        allowed = self._registry.authorize(task_id, caller)
        if not allowed:
            logger.warning(
                "governed_task_access_denied",
                task_id=task_id,
                tenant_id=caller.tenant_id,
            )
        return allowed

    def _require_authorized(self, task_id: str) -> None:
        if not self._authorized(task_id):
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Task not found: {task_id}",
                )
            )


__all__ = ["GovernedTaskStore"]
