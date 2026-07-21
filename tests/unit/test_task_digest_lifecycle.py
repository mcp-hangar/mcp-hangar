"""Unit tests for the relay-ledger tool-digest lifecycle (ADR-014 / #320).

Covers :class:`GovernedTaskStore` binding a relayed task to the tool digest
pinned on the invoke path (via the current-tool contextvar) and re-verifying the
tool's CURRENT digest fail-closed. On drift -- or an unverifiable schema -- the
task is failed and a ``DigestMismatchInTask`` + ``TaskFailed`` are emitted onto
the provenance chain (there is no stored result: the ledger relays governance
metadata only).

The per-tenant tool projection lookup is faked so the "current" schema is
deterministic and independent of any discovery state.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from mcp_hangar._sdk_compat import McpError
from mcp_hangar.application.tasks import governed_task_store as gts_module
from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.application.tasks.tool_pin_context import (
    CurrentToolPin,
    clear_current_tool_pin,
    set_current_tool_pin,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.events import DigestMismatchInTask, TaskFailed
from mcp_hangar.domain.services.digest_computation import compute_tool_digest
from mcp_hangar.domain.services.task_ownership import TaskOwner
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext

_SERVER = "S1"
_MCP_SERVER = "backend-a"
_TOOL = "read_item"

# Two distinct schemas for the same tool -> two distinct digests (drift).
_SCHEMA_V1: dict[str, Any] = {
    "name": _TOOL,
    "description": "Read an item by id",
    "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}},
}
_SCHEMA_V2: dict[str, Any] = {
    "name": _TOOL,
    "description": "Read an item by id (v2 -- schema drifted)",
    "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}}},
}


class _FakeProjection:
    def __init__(self, schema: dict[str, Any]) -> None:
        self.schema = schema


class _FakeRegistry:
    """Minimal stand-in for ToolProjectionRegistry.resolve()."""

    def __init__(self, schema: dict[str, Any] | None) -> None:
        self._schema = schema

    def resolve(self, mcp_server: str, tool: str, tenant_id: str | None = None) -> _FakeProjection | None:
        if self._schema is None:
            return None
        return _FakeProjection(self._schema)


@contextmanager
def _as(tenant_id: str | None, principal_id: str | None = None) -> Iterator[None]:
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


@contextmanager
def _current_tool(pinned_digest: str) -> Iterator[None]:
    set_current_tool_pin(CurrentToolPin(mcp_server=_MCP_SERVER, tool_name=_TOOL, pinned_digest=pinned_digest))
    try:
        yield
    finally:
        clear_current_tool_pin()


def _upstream(task_id: str) -> dict[str, Any]:
    return {
        "taskId": task_id,
        "status": "working",
        "createdAt": "2020-01-01T00:00:00Z",
        "lastUpdatedAt": "2020-01-01T00:00:00Z",
        "ttl": 60_000,
    }


def _register_pinned(store: GovernedTaskStore, task_id: str, pinned: str) -> tuple[str, str]:
    with _as("tenant-a", "alice"), _current_tool(pinned):
        task = store.mint_from_upstream(_upstream(task_id))
        store.register_relayed_task(
            target_server_id=_SERVER,
            task=task,
            expected_owner=TaskOwner("tenant-a", "alice"),
        )
    return (_SERVER, task_id)


@pytest.fixture
def events() -> list[object]:
    return []


@pytest.fixture
def store(events: list[object]) -> GovernedTaskStore:
    return GovernedTaskStore(event_publisher=events.append)


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch):
    """Patch the projection lookup used by digest re-verification; returns a setter."""

    def _install(schema: dict[str, Any] | None) -> None:
        monkeypatch.setattr(gts_module, "get_tool_projection_registry", lambda: _FakeRegistry(schema))

    return _install


def test_register_under_pin_binds_digest(store: GovernedTaskStore) -> None:
    pinned = compute_tool_digest(_SCHEMA_V1).sha256
    key = _register_pinned(store, "T1", pinned)
    assert store.digest_guard.verify(key, pinned) is True
    assert store.digest_guard.verify(key, "0" * 64) is False


def test_verify_passes_when_digest_matches(store: GovernedTaskStore, fake_registry: Any) -> None:
    pinned = compute_tool_digest(_SCHEMA_V1).sha256
    key = _register_pinned(store, "T1", pinned)
    fake_registry(_SCHEMA_V1)  # current schema unchanged -> digest matches
    store._verify_pinned_digest(key)  # no raise
    with _as("tenant-a", "alice"):
        task = store.get_task(key)
    assert task is not None and task.status == "working"


def test_verify_fails_closed_on_digest_drift(
    store: GovernedTaskStore,
    fake_registry: Any,
    events: list[object],
) -> None:
    pinned = compute_tool_digest(_SCHEMA_V1).sha256
    key = _register_pinned(store, "T1", pinned)

    fake_registry(_SCHEMA_V2)  # tool schema drifted since relay
    with pytest.raises(McpError, match="tool digest drifted"):
        store._verify_pinned_digest(key)

    # Task was failed closed and both provenance events emitted.
    with _as("tenant-a", "alice"):
        task = store.get_task(key)
    assert task is not None and task.status == "failed"

    mismatches = [e for e in events if isinstance(e, DigestMismatchInTask)]
    failures = [e for e in events if isinstance(e, TaskFailed)]
    assert len(mismatches) == 1
    assert mismatches[0].task_id == "T1"
    assert mismatches[0].target_server_id == _SERVER
    assert mismatches[0].expected_digest == pinned
    assert mismatches[0].observed_digest == compute_tool_digest(_SCHEMA_V2).sha256
    assert mismatches[0].mcp_server_id == _MCP_SERVER
    assert mismatches[0].tool_name == _TOOL
    assert len(failures) == 1
    assert failures[0].task_id == "T1"
    assert failures[0].error_type == "digest_drift"


def test_verify_fails_closed_when_tool_unresolvable(
    store: GovernedTaskStore,
    fake_registry: Any,
    events: list[object],
) -> None:
    pinned = compute_tool_digest(_SCHEMA_V1).sha256
    key = _register_pinned(store, "T1", pinned)

    fake_registry(None)  # tool no longer resolvable -> observed None -> fail closed
    with pytest.raises(McpError, match="tool digest drifted"):
        store._verify_pinned_digest(key)

    with _as("tenant-a", "alice"):
        task = store.get_task(key)
    assert task is not None and task.status == "failed"

    mismatches = [e for e in events if isinstance(e, DigestMismatchInTask)]
    assert len(mismatches) == 1
    assert mismatches[0].observed_digest is None


def test_no_pin_is_never_gated(store: GovernedTaskStore, fake_registry: Any, events: list[object]) -> None:
    # Relayed without a current-tool pin -> no digest binding, verify is a no-op.
    with _as("tenant-a", "alice"):
        task = store.mint_from_upstream(_upstream("T1"))
        store.register_relayed_task(
            target_server_id=_SERVER,
            task=task,
            expected_owner=TaskOwner("tenant-a", "alice"),
        )
    key = (_SERVER, "T1")

    fake_registry(_SCHEMA_V2)  # even a "drift" cannot gate an unpinned task
    store._verify_pinned_digest(key)  # no raise

    assert store.digest_guard.verify(key, "0" * 64) is False
    assert not [e for e in events if isinstance(e, (DigestMismatchInTask, TaskFailed))]
    with _as("tenant-a", "alice"):
        task2 = store.get_task(key)
    assert task2 is not None and task2.status == "working"
