"""Unit tests for :class:`GovernedTaskStore`.

These tests wrap a real :class:`InMemoryTaskStore` and drive identity via the
``identity_context_var`` contextvar to assert cross-tenant fail-closed denial
and no task leakage across tenants.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from mcp.shared.exceptions import McpError
from mcp.shared.experimental.tasks.in_memory_task_store import InMemoryTaskStore
from mcp.types import TaskMetadata
import pytest

from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext


@contextmanager
def _as(tenant_id: str | None, principal_id: str | None = None) -> Iterator[None]:
    """Bind the identity contextvar to a given tenant/principal for a block."""
    if tenant_id is None and principal_id is None:
        token = identity_context_var.set(None)
    else:
        caller = CallerIdentity(
            user_id=principal_id,
            agent_id=None,
            session_id=None,
            principal_type="user" if principal_id else "anonymous",
            tenant_id=tenant_id,
        )
        token = identity_context_var.set(IdentityContext(caller=caller))
    try:
        yield
    finally:
        identity_context_var.reset(token)


@pytest.fixture
def store() -> GovernedTaskStore:
    return GovernedTaskStore(inner=InMemoryTaskStore())


async def _create(store: GovernedTaskStore) -> str:
    task = await store.create_task(TaskMetadata(ttl=60_000))
    return task.taskId


async def test_owner_can_read_update_delete(store: GovernedTaskStore) -> None:
    with _as("tenant-a", "alice"):
        task_id = await _create(store)
        assert (await store.get_task(task_id)) is not None
        updated = await store.update_task(task_id, status="completed")
        assert updated.status == "completed"
        assert await store.delete_task(task_id) is True


async def test_cross_tenant_get_denied_returns_none(store: GovernedTaskStore) -> None:
    with _as("tenant-a", "alice"):
        task_id = await _create(store)
    # Tenant B must not see tenant A's task.
    with _as("tenant-b", "bob"):
        assert (await store.get_task(task_id)) is None
        assert (await store.get_result(task_id)) is None


async def test_cross_tenant_mutations_fail_closed(store: GovernedTaskStore) -> None:
    with _as("tenant-a", "alice"):
        task_id = await _create(store)
    with _as("tenant-b", "bob"):
        with pytest.raises(McpError):
            await store.update_task(task_id, status="completed")
        with pytest.raises(McpError):
            await store.delete_task(task_id)
    # The task is untouched and still owned by tenant A.
    with _as("tenant-a", "alice"):
        task = await store.get_task(task_id)
        assert task is not None
        assert task.status == "working"


async def test_same_tenant_different_principal_denied(store: GovernedTaskStore) -> None:
    with _as("tenant-a", "alice"):
        task_id = await _create(store)
    with _as("tenant-a", "mallory"):
        assert (await store.get_task(task_id)) is None
        with pytest.raises(McpError):
            await store.delete_task(task_id)


async def test_unknown_task_id_denied(store: GovernedTaskStore) -> None:
    with _as("tenant-a", "alice"):
        assert (await store.get_task("does-not-exist")) is None
        with pytest.raises(McpError):
            await store.update_task("does-not-exist", status="completed")
        with pytest.raises(McpError):
            await store.delete_task("does-not-exist")


async def test_list_tasks_returns_only_callers_tasks(store: GovernedTaskStore) -> None:
    with _as("tenant-a", "alice"):
        a1 = await _create(store)
        a2 = await _create(store)
    with _as("tenant-b", "bob"):
        b1 = await _create(store)

    with _as("tenant-a", "alice"):
        tasks, cursor = await store.list_tasks()
        assert cursor is None
        assert {t.taskId for t in tasks} == {a1, a2}

    with _as("tenant-b", "bob"):
        tasks, _ = await store.list_tasks()
        assert {t.taskId for t in tasks} == {b1}


async def test_anonymous_caller_cannot_reach_attributed_task(store: GovernedTaskStore) -> None:
    with _as("tenant-a", "alice"):
        task_id = await _create(store)
    # No identity bound -> unattributed caller (None, None) is denied.
    with _as(None, None):
        assert (await store.get_task(task_id)) is None
        tasks, _ = await store.list_tasks()
        assert tasks == []


async def test_anonymous_create_and_read_roundtrip(store: GovernedTaskStore) -> None:
    # The system/anonymous path may create and read its own unattributed tasks.
    with _as(None, None):
        task_id = await _create(store)
        assert (await store.get_task(task_id)) is not None
        tasks, _ = await store.list_tasks()
        assert {t.taskId for t in tasks} == {task_id}
