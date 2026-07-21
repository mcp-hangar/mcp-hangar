"""Unit tests for the v2-native :class:`GovernedTaskStore` governance ledger.

Drives identity via the ``identity_context_var`` contextvar to assert the
fail-closed, composite-keyed governance ledger: authorize-first on every public
path, per-``(target_server_id, task_id)`` isolation, cross-tenant denial with no
existence leak, verbatim upstream minting, and eviction fail-close.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from mcp_hangar._sdk_compat import McpError
from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.events import TaskFailed
from mcp_hangar.domain.services.task_ownership import (
    TaskOwner,
    TaskOwnerConflictError,
    TaskOwnershipRegistry,
)
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


def _upstream(task_id: str, *, status: str = "working", **extra: Any) -> dict[str, Any]:
    """A minimal well-formed upstream task dict (camelCase wire aliases)."""
    data: dict[str, Any] = {
        "taskId": task_id,
        "status": status,
        "createdAt": "2020-01-01T00:00:00Z",
        "lastUpdatedAt": "2020-01-01T00:00:00Z",
        "ttl": 60_000,
    }
    data.update(extra)
    return data


def _register(
    store: GovernedTaskStore,
    server: str,
    task_id: str,
    tenant: str | None,
    principal: str | None = None,
    *,
    status: str = "working",
) -> None:
    """Relay one task under the given identity."""
    with _as(tenant, principal):
        task = store.mint_from_upstream(_upstream(task_id, status=status))
        store.register_relayed_task(
            target_server_id=server,
            task=task,
            expected_owner=TaskOwner(tenant, principal),
        )


@pytest.fixture
def events() -> list[object]:
    return []


@pytest.fixture
def store(events: list[object]) -> GovernedTaskStore:
    return GovernedTaskStore(event_publisher=events.append)


# -- authorize-first chokepoint ------------------------------------------------


def test_authorize_gates_every_public_method_fail_closed(
    store: GovernedTaskStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    key = ("S1", "T1")

    calls: list[tuple[str, str]] = []
    real = store.authorize

    def _spy(k: tuple[str, str]) -> bool:
        calls.append(k)
        return real(k)

    monkeypatch.setattr(store, "authorize", _spy)

    with _as("tenant-a", "alice"):
        assert store.get_task(key) is not None
        store.update_snapshot(key, status="completed")
        store.delete_task(key)

    # Every public single-key path routed through the authorize chokepoint.
    assert calls == [key, key, key]


def test_denied_read_leaks_nothing_and_mutation_raises(
    store: GovernedTaskStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    key = ("S1", "T1")
    # Force denial: the ledger must never be touched past the gate.
    monkeypatch.setattr(store, "authorize", lambda k: False)

    with _as("tenant-a", "alice"):
        assert store.get_task(key) is None
        with pytest.raises(McpError, match="Task not found: T1"):
            store.update_snapshot(key, status="completed")
        with pytest.raises(McpError, match="Task not found: T1"):
            store.delete_task(key)


# -- composite-key isolation ---------------------------------------------------


def test_same_task_id_different_server_coexist_and_authorize_independently(
    store: GovernedTaskStore,
) -> None:
    _register(store, "S1", "T1", "tenant-x", "xavier")
    _register(store, "S2", "T1", "tenant-y", "yolanda")

    with _as("tenant-x", "xavier"):
        assert store.get_task(("S1", "T1")) is not None
        assert store.get_task(("S2", "T1")) is None  # not tenant-x's

    with _as("tenant-y", "yolanda"):
        assert store.get_task(("S2", "T1")) is not None
        assert store.get_task(("S1", "T1")) is None  # not tenant-y's


def test_cross_owner_rebind_of_live_key_fails_closed(store: GovernedTaskStore) -> None:
    _register(store, "S1", "T1", "tenant-x", "xavier")
    with _as("tenant-z", "zach"):
        task = store.mint_from_upstream(_upstream("T1"))
        with pytest.raises(TaskOwnerConflictError):
            store.register_relayed_task(
                target_server_id="S1",
                task=task,
                expected_owner=TaskOwner("tenant-z", "zach"),
            )


def test_relay_identity_divergence_fails_closed(store: GovernedTaskStore) -> None:
    with _as("tenant-a", "alice"):
        task = store.mint_from_upstream(_upstream("T1"))
        with pytest.raises(ValueError, match="relay identity diverged"):
            store.register_relayed_task(
                target_server_id="S1",
                task=task,
                expected_owner=TaskOwner("tenant-b", "bob"),
            )


# -- cross-tenant matrix -------------------------------------------------------


def test_cross_tenant_reads_return_none(store: GovernedTaskStore) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    with _as("tenant-b", "bob"):
        assert store.get_task(("S1", "T1")) is None


def test_cross_tenant_mutations_fail_closed_and_leave_task_intact(
    store: GovernedTaskStore,
) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    key = ("S1", "T1")
    with _as("tenant-b", "bob"):
        with pytest.raises(McpError, match="Task not found: T1"):
            store.update_snapshot(key, status="completed")
        with pytest.raises(McpError, match="Task not found: T1"):
            store.delete_task(key)
    with _as("tenant-a", "alice"):
        task = store.get_task(key)
        assert task is not None
        assert task.status == "working"


def test_same_tenant_different_principal_denied(store: GovernedTaskStore) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    key = ("S1", "T1")
    with _as("tenant-a", "mallory"):
        assert store.get_task(key) is None
        with pytest.raises(McpError):
            store.delete_task(key)


def test_list_tasks_excludes_others_and_never_forwards_cursor(
    store: GovernedTaskStore,
) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    _register(store, "S1", "T2", "tenant-a", "alice")
    _register(store, "S1", "T3", "tenant-b", "bob")

    with _as("tenant-a", "alice"):
        tasks, cursor = store.list_tasks()
        assert cursor is None
        assert {t.task_id for t in tasks} == {"T1", "T2"}

    with _as("tenant-b", "bob"):
        tasks, cursor = store.list_tasks()
        assert cursor is None
        assert {t.task_id for t in tasks} == {"T3"}


def test_anonymous_caller_cannot_reach_attributed_task(store: GovernedTaskStore) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    with _as(None, None):
        assert store.get_task(("S1", "T1")) is None
        tasks, _ = store.list_tasks()
        assert tasks == []


# -- mint_from_upstream --------------------------------------------------------


def test_mint_id_precedence_taskid_over_task_id_over_id(store: GovernedTaskStore) -> None:
    # All three present -> taskId wins.
    t_all = store.mint_from_upstream(
        {
            "taskId": "A",
            "task_id": "B",
            "id": "C",
            "status": "working",
            "createdAt": "x",
            "lastUpdatedAt": "y",
            "ttl": 1,
        }
    )
    assert t_all.task_id == "A"
    # taskId absent -> task_id wins over id.
    t = store.mint_from_upstream(
        {"task_id": "B", "id": "C", "status": "working", "createdAt": "x", "lastUpdatedAt": "y", "ttl": 1}
    )
    assert t.task_id == "B"
    t2 = store.mint_from_upstream({"id": "C", "status": "working", "createdAt": "x", "lastUpdatedAt": "y", "ttl": 1})
    assert t2.task_id == "C"


def test_mint_missing_id_fails_closed(store: GovernedTaskStore) -> None:
    with pytest.raises(ValueError, match="missing a task id"):
        store.mint_from_upstream({"status": "working", "createdAt": "x", "lastUpdatedAt": "y", "ttl": 1})


def test_mint_missing_required_field_fails_closed(store: GovernedTaskStore) -> None:
    # No status -> malformed upstream, no synthesized default.
    with pytest.raises(ValueError):
        store.mint_from_upstream({"taskId": "T1", "createdAt": "x", "lastUpdatedAt": "y", "ttl": 1})


def test_mint_carries_upstream_fields_verbatim(store: GovernedTaskStore) -> None:
    up = _upstream("T1", status="input_required", statusMessage="need input", pollInterval=500)
    task = store.mint_from_upstream(up)
    assert task.status == "input_required"
    assert task.status_message == "need input"
    assert task.created_at == "2020-01-01T00:00:00Z"
    assert task.last_updated_at == "2020-01-01T00:00:00Z"
    assert task.ttl == 60_000
    assert task.poll_interval == 500


def test_relayed_at_is_local_and_not_upstream_created_at(store: GovernedTaskStore) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    entry = store._tasks[("S1", "T1")]
    # relayed_at is Hangar's local clock, distinct from the upstream created_at.
    assert entry.relayed_at
    assert entry.relayed_at != entry.snapshot.created_at
    assert entry.snapshot.created_at == "2020-01-01T00:00:00Z"


# -- eviction safety -----------------------------------------------------------


def test_eviction_fails_live_entry_closed_and_publishes(events: list[object]) -> None:
    store = GovernedTaskStore(event_publisher=events.append)
    # Shrink the ownership registry so the 3rd relay evicts the 1st (LRU cap),
    # wiring on_evict back to this store's fail-close callback.
    store._registry = TaskOwnershipRegistry(maxsize=2, on_evict=store._on_evict)

    _register(store, "S", "T1", "tenant-a", "alice")
    _register(store, "S", "T2", "tenant-a", "alice")
    _register(store, "S", "T3", "tenant-a", "alice")  # evicts ("S","T1")

    failed = [e for e in events if isinstance(e, TaskFailed)]
    assert [e.task_id for e in failed] == ["T1"]
    assert failed[0].error_type == "evicted"
    assert failed[0].tenant_id == "tenant-a"

    # The evicted entry is purged from the ledger (fail-closed, no leak).
    with _as("tenant-a", "alice"):
        assert store.get_task(("S", "T1")) is None
        assert store.get_task(("S", "T3")) is not None
