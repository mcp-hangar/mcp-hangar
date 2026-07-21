"""Unit tests for the fail-closed :class:`TaskOwnershipRegistry`."""

from __future__ import annotations

import pytest

from mcp_hangar.domain.services.task_ownership import (
    TaskOwner,
    TaskOwnerConflictError,
    TaskOwnershipRegistry,
)

# Composite keys are (target_server_id, task_id).
_K1 = ("srv-a", "task-1")
_K2 = ("srv-a", "task-2")
_K3 = ("srv-a", "task-3")


def test_register_and_authorize_same_owner() -> None:
    reg = TaskOwnershipRegistry()
    owner = TaskOwner(tenant_id="tenant-a", principal_id="alice")
    reg.register(_K1, owner)
    assert reg.authorize(_K1, TaskOwner(tenant_id="tenant-a", principal_id="alice")) is True


def test_same_task_id_different_server_is_distinct() -> None:
    """task_id is unique only per upstream: the same task_id on two servers
    is two independent bindings and must not cross-authorize."""
    reg = TaskOwnershipRegistry()
    reg.register(("srv-a", "task-1"), TaskOwner(tenant_id="tenant-a", principal_id="alice"))
    reg.register(("srv-b", "task-1"), TaskOwner(tenant_id="tenant-b", principal_id="bob"))
    assert reg.authorize(("srv-a", "task-1"), TaskOwner(tenant_id="tenant-a", principal_id="alice")) is True
    assert reg.authorize(("srv-b", "task-1"), TaskOwner(tenant_id="tenant-b", principal_id="bob")) is True
    # An owner of one server's task cannot reach the other server's identically named task.
    assert reg.authorize(("srv-b", "task-1"), TaskOwner(tenant_id="tenant-a", principal_id="alice")) is False


def test_different_tenant_denied() -> None:
    reg = TaskOwnershipRegistry()
    reg.register(_K1, TaskOwner(tenant_id="tenant-a", principal_id="alice"))
    # Same principal id, different tenant: cross-tenant access must fail closed.
    assert reg.authorize(_K1, TaskOwner(tenant_id="tenant-b", principal_id="alice")) is False


def test_different_principal_same_tenant_denied() -> None:
    reg = TaskOwnershipRegistry()
    reg.register(_K1, TaskOwner(tenant_id="tenant-a", principal_id="alice"))
    assert reg.authorize(_K1, TaskOwner(tenant_id="tenant-a", principal_id="bob")) is False


def test_unknown_task_id_fail_closed() -> None:
    reg = TaskOwnershipRegistry()
    assert reg.authorize(("srv-a", "never-registered"), TaskOwner(tenant_id="tenant-a", principal_id="alice")) is False


def test_owner_without_principal_authorizes_any_principal_same_tenant() -> None:
    """An owner registered with principal_id=None grants access to any
    principal of the same tenant. This is a deliberate choice: when the
    principal dimension is not established at task creation, authorization
    falls back to tenant-scoping only. Tenant isolation is still enforced.
    """
    reg = TaskOwnershipRegistry()
    reg.register(_K1, TaskOwner(tenant_id="tenant-a", principal_id=None))
    assert reg.authorize(_K1, TaskOwner(tenant_id="tenant-a", principal_id="alice")) is True
    assert reg.authorize(_K1, TaskOwner(tenant_id="tenant-a", principal_id="bob")) is True
    # Tenant isolation is still enforced.
    assert reg.authorize(_K1, TaskOwner(tenant_id="tenant-b", principal_id="alice")) is False


def test_caller_principal_none_denied_when_owner_has_principal() -> None:
    reg = TaskOwnershipRegistry()
    reg.register(_K1, TaskOwner(tenant_id="tenant-a", principal_id="alice"))
    assert reg.authorize(_K1, TaskOwner(tenant_id="tenant-a", principal_id=None)) is False


def test_empty_task_id_register_raises() -> None:
    reg = TaskOwnershipRegistry()
    with pytest.raises(ValueError):
        reg.register(("srv-a", ""), TaskOwner(tenant_id="tenant-a", principal_id="alice"))


def test_empty_server_id_register_raises() -> None:
    reg = TaskOwnershipRegistry()
    with pytest.raises(ValueError):
        reg.register(("", "task-1"), TaskOwner(tenant_id="tenant-a", principal_id="alice"))


def test_empty_task_id_authorize_fail_closed() -> None:
    reg = TaskOwnershipRegistry()
    assert reg.authorize(("srv-a", ""), TaskOwner(tenant_id="tenant-a", principal_id="alice")) is False


def test_reregister_same_owner_is_idempotent() -> None:
    reg = TaskOwnershipRegistry()
    owner = TaskOwner(tenant_id="tenant-a", principal_id="alice")
    reg.register(_K1, owner)
    reg.register(_K1, owner)  # Same owner: refresh, no raise.
    assert reg.authorize(_K1, owner) is True


def test_reregister_different_owner_fails_closed() -> None:
    """Re-binding a live key to a different owner must fail closed, not clobber."""
    reg = TaskOwnershipRegistry()
    reg.register(_K1, TaskOwner(tenant_id="tenant-a", principal_id="alice"))
    with pytest.raises(TaskOwnerConflictError):
        reg.register(_K1, TaskOwner(tenant_id="tenant-b", principal_id="bob"))
    # Original binding is preserved; the interloper never gained access.
    assert reg.authorize(_K1, TaskOwner(tenant_id="tenant-a", principal_id="alice")) is True
    assert reg.authorize(_K1, TaskOwner(tenant_id="tenant-b", principal_id="bob")) is False


def test_conflict_error_is_value_error() -> None:
    reg = TaskOwnershipRegistry()
    reg.register(_K1, TaskOwner(tenant_id="tenant-a", principal_id="alice"))
    with pytest.raises(ValueError):  # TaskOwnerConflictError subclasses ValueError.
        reg.register(_K1, TaskOwner(tenant_id="tenant-b", principal_id="bob"))


def test_expired_entry_fail_closed() -> None:
    # Zero TTL: any elapsed time expires the entry, so access fails closed.
    reg = TaskOwnershipRegistry(ttl=0.0)
    reg.register(_K1, TaskOwner(tenant_id="tenant-a", principal_id="alice"))
    assert reg.authorize(_K1, TaskOwner(tenant_id="tenant-a", principal_id="alice")) is False


def test_discard_removes_authorization() -> None:
    reg = TaskOwnershipRegistry()
    owner = TaskOwner(tenant_id="tenant-a", principal_id="alice")
    reg.register(_K1, owner)
    reg.discard(_K1)
    assert reg.authorize(_K1, owner) is False
    # Discarding an unknown id is a no-op.
    reg.discard(_K1)


def test_discard_does_not_fire_on_evict() -> None:
    evicted: list[tuple[str, str]] = []
    reg = TaskOwnershipRegistry(on_evict=evicted.append)
    owner = TaskOwner(tenant_id="tenant-a", principal_id="alice")
    reg.register(_K1, owner)
    reg.discard(_K1)
    assert evicted == []  # Deliberate terminal removal: no eviction callback.


def test_clear_removes_all() -> None:
    reg = TaskOwnershipRegistry()
    owner = TaskOwner(tenant_id="tenant-a", principal_id="alice")
    reg.register(_K1, owner)
    reg.register(_K2, owner)
    reg.clear()
    assert reg.authorize(_K1, owner) is False
    assert reg.authorize(_K2, owner) is False


def test_lru_eviction_on_maxsize() -> None:
    reg = TaskOwnershipRegistry(maxsize=2)
    owner = TaskOwner(tenant_id="tenant-a", principal_id="alice")
    reg.register(_K1, owner)
    reg.register(_K2, owner)
    reg.register(_K3, owner)  # Evicts the oldest (_K1).
    assert reg.authorize(_K1, owner) is False
    assert reg.authorize(_K2, owner) is True
    assert reg.authorize(_K3, owner) is True


def test_on_evict_fires_on_lru_eviction() -> None:
    """Filling past the cap evicts a still-live entry; the callback must receive
    its key so the caller (governance ledger) can fail-close the task."""
    evicted: list[tuple[str, str]] = []
    reg = TaskOwnershipRegistry(maxsize=2, on_evict=evicted.append)
    owner = TaskOwner(tenant_id="tenant-a", principal_id="alice")
    reg.register(_K1, owner)
    reg.register(_K2, owner)
    reg.register(_K3, owner)  # Evicts oldest live entry (_K1).
    assert evicted == [_K1]


def test_on_evict_fires_on_lazy_ttl_expiry() -> None:
    """An entry found expired on access is evicted and fires the callback."""
    evicted: list[tuple[str, str]] = []
    reg = TaskOwnershipRegistry(ttl=0.0, on_evict=evicted.append)
    owner = TaskOwner(tenant_id="tenant-a", principal_id="alice")
    reg.register(_K1, owner)
    assert reg.authorize(_K1, owner) is False  # Expired -> deny + evict.
    assert evicted == [_K1]


def test_on_evict_failure_does_not_break_registry() -> None:
    def boom(_key: tuple[str, str]) -> None:
        raise RuntimeError("ledger down")

    reg = TaskOwnershipRegistry(maxsize=1, on_evict=boom)
    owner = TaskOwner(tenant_id="tenant-a", principal_id="alice")
    reg.register(_K1, owner)
    reg.register(_K2, owner)  # Evicts _K1; callback raises but is swallowed.
    assert reg.authorize(_K2, owner) is True
