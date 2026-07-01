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

Stages: this is Stage 1 (bind + authorize). Digest re-check on ``store_result``
is Stage 2 (#320); consent gating is a later stage (#322).
"""

from __future__ import annotations

from mcp.shared.exceptions import McpError
from mcp.shared.experimental.tasks.in_memory_task_store import InMemoryTaskStore
from mcp.shared.experimental.tasks.store import TaskStore
from mcp.types import INVALID_PARAMS, ErrorData, Result, Task, TaskMetadata, TaskStatus

from mcp_hangar.context import get_identity_context
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
    ) -> None:
        self._inner: TaskStore = inner if inner is not None else InMemoryTaskStore()
        self._registry: TaskOwnershipRegistry = registry if registry is not None else TaskOwnershipRegistry()

    @property
    def registry(self) -> TaskOwnershipRegistry:
        """The ownership registry backing this store (for shared wiring/tests)."""
        return self._registry

    async def create_task(
        self,
        metadata: TaskMetadata,
        task_id: str | None = None,
    ) -> Task:
        """Create a task on the inner store and bind it to the current caller."""
        task = await self._inner.create_task(metadata, task_id)
        owner = _current_caller()
        self._registry.register(task.taskId, owner)
        logger.debug(
            "governed_task_created",
            task_id=task.taskId,
            tenant_id=owner.tenant_id,
            has_principal=owner.principal_id is not None,
        )
        return task

    async def get_task(self, task_id: str) -> Task | None:
        """Return the task only if the current caller owns it, else ``None``."""
        if not self._authorized(task_id):
            return None
        return await self._inner.get_task(task_id)

    async def get_result(self, task_id: str) -> Result | None:
        """Return the task result only if the current caller owns it, else ``None``."""
        if not self._authorized(task_id):
            return None
        return await self._inner.get_result(task_id)

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

        Stage 2 (#320) will re-check the pinned response digest here before
        storing a task result; Stage 1 only delegates.
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
