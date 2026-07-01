"""Unit tests for the fail-closed :class:`TaskOwnershipRegistry`."""

from __future__ import annotations

import pytest

from mcp_hangar.domain.services.task_ownership import TaskOwner, TaskOwnershipRegistry


def test_register_and_authorize_same_owner() -> None:
    reg = TaskOwnershipRegistry()
    owner = TaskOwner(tenant_id="tenant-a", principal_id="alice")
    reg.register("task-1", owner)
    assert reg.authorize("task-1", TaskOwner(tenant_id="tenant-a", principal_id="alice")) is True


def test_different_tenant_denied() -> None:
    reg = TaskOwnershipRegistry()
    reg.register("task-1", TaskOwner(tenant_id="tenant-a", principal_id="alice"))
    # Same principal id, different tenant: cross-tenant access must fail closed.
    assert reg.authorize("task-1", TaskOwner(tenant_id="tenant-b", principal_id="alice")) is False


def test_different_principal_same_tenant_denied() -> None:
    reg = TaskOwnershipRegistry()
    reg.register("task-1", TaskOwner(tenant_id="tenant-a", principal_id="alice"))
    assert reg.authorize("task-1", TaskOwner(tenant_id="tenant-a", principal_id="bob")) is False


def test_unknown_task_id_fail_closed() -> None:
    reg = TaskOwnershipRegistry()
    assert reg.authorize("never-registered", TaskOwner(tenant_id="tenant-a", principal_id="alice")) is False


def test_owner_without_principal_authorizes_any_principal_same_tenant() -> None:
    """An owner registered with principal_id=None grants access to any
    principal of the same tenant. This is a deliberate choice: when the
    principal dimension is not established at task creation, authorization
    falls back to tenant-scoping only. Tenant isolation is still enforced.
    """
    reg = TaskOwnershipRegistry()
    reg.register("task-1", TaskOwner(tenant_id="tenant-a", principal_id=None))
    assert reg.authorize("task-1", TaskOwner(tenant_id="tenant-a", principal_id="alice")) is True
    assert reg.authorize("task-1", TaskOwner(tenant_id="tenant-a", principal_id="bob")) is True
    # Tenant isolation is still enforced.
    assert reg.authorize("task-1", TaskOwner(tenant_id="tenant-b", principal_id="alice")) is False


def test_caller_principal_none_denied_when_owner_has_principal() -> None:
    reg = TaskOwnershipRegistry()
    reg.register("task-1", TaskOwner(tenant_id="tenant-a", principal_id="alice"))
    assert reg.authorize("task-1", TaskOwner(tenant_id="tenant-a", principal_id=None)) is False


def test_empty_task_id_register_raises() -> None:
    reg = TaskOwnershipRegistry()
    with pytest.raises(ValueError):
        reg.register("", TaskOwner(tenant_id="tenant-a", principal_id="alice"))


def test_empty_task_id_authorize_fail_closed() -> None:
    reg = TaskOwnershipRegistry()
    assert reg.authorize("", TaskOwner(tenant_id="tenant-a", principal_id="alice")) is False


def test_expired_entry_fail_closed() -> None:
    # Zero TTL: any elapsed time expires the entry, so access fails closed.
    reg = TaskOwnershipRegistry(ttl=0.0)
    reg.register("task-1", TaskOwner(tenant_id="tenant-a", principal_id="alice"))
    assert reg.authorize("task-1", TaskOwner(tenant_id="tenant-a", principal_id="alice")) is False


def test_discard_removes_authorization() -> None:
    reg = TaskOwnershipRegistry()
    owner = TaskOwner(tenant_id="tenant-a", principal_id="alice")
    reg.register("task-1", owner)
    reg.discard("task-1")
    assert reg.authorize("task-1", owner) is False
    # Discarding an unknown id is a no-op.
    reg.discard("task-1")


def test_clear_removes_all() -> None:
    reg = TaskOwnershipRegistry()
    owner = TaskOwner(tenant_id="tenant-a", principal_id="alice")
    reg.register("task-1", owner)
    reg.register("task-2", owner)
    reg.clear()
    assert reg.authorize("task-1", owner) is False
    assert reg.authorize("task-2", owner) is False


def test_lru_eviction_on_maxsize() -> None:
    reg = TaskOwnershipRegistry(maxsize=2)
    owner = TaskOwner(tenant_id="tenant-a", principal_id="alice")
    reg.register("task-1", owner)
    reg.register("task-2", owner)
    reg.register("task-3", owner)  # Evicts the oldest (task-1).
    assert reg.authorize("task-1", owner) is False
    assert reg.authorize("task-2", owner) is True
    assert reg.authorize("task-3", owner) is True
