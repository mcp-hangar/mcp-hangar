"""Unit tests for Stage 2 task digest lifecycle (#320).

Covers :class:`GovernedTaskStore` binding a task to the tool digest pinned on
the invoke path (via the current-tool contextvar) and re-verifying the tool's
CURRENT digest fail-closed when the result is retrieved.

The per-tenant tool projection lookup is faked so the "current" schema is
deterministic and independent of any discovery state.
"""

from __future__ import annotations

import pytest

# GovernedTaskStore builds on the SDK v1 experimental task store, which SDK v2
# removed (its v2 rebuild is tracked in #322). Skip this module on v2.
pytest.importorskip(
    "mcp.shared.experimental.tasks.store",
    reason="v1-only dormant task governance; v2 rebuild in #322",
)

from collections.abc import Iterator  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from typing import Any  # noqa: E402

from mcp.shared.experimental.tasks.in_memory_task_store import InMemoryTaskStore  # noqa: E402

from mcp_hangar._sdk_compat import McpError, Result, TaskMetadata  # noqa: E402
from mcp_hangar.application.tasks import governed_task_store as gts_module  # noqa: E402
from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.application.tasks.tool_pin_context import (
    CurrentToolPin,
    clear_current_tool_pin,
    set_current_tool_pin,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.services.digest_computation import compute_tool_digest
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext

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

    def resolve(
        self,
        mcp_server: str,
        tool: str,
        tenant_id: str | None = None,
    ) -> _FakeProjection | None:
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


@pytest.fixture
def store() -> GovernedTaskStore:
    return GovernedTaskStore(inner=InMemoryTaskStore())


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch):
    """Patch the projection lookup used by get_result; returns a setter."""

    def _install(schema: dict[str, Any] | None) -> None:
        monkeypatch.setattr(
            gts_module,
            "get_tool_projection_registry",
            lambda: _FakeRegistry(schema),
        )

    return _install


async def test_create_with_current_tool_pins_task(store: GovernedTaskStore) -> None:
    pinned = compute_tool_digest(_SCHEMA_V1).sha256
    with _as("tenant-a", "alice"), _current_tool(pinned):
        task = await store.create_task(TaskMetadata(ttl=60_000))

    # The shared guard now holds a pin for this task, and it matches the digest
    # that was current on the invoke path.
    assert store.digest_guard.verify(task.taskId, pinned) is True
    assert store.digest_guard.verify(task.taskId, "0" * 64) is False


async def test_get_result_returns_when_digest_matches(
    store: GovernedTaskStore,
    fake_registry: Any,
) -> None:
    pinned = compute_tool_digest(_SCHEMA_V1).sha256
    with _as("tenant-a", "alice"), _current_tool(pinned):
        task = await store.create_task(TaskMetadata(ttl=60_000))
        await store.store_result(task.taskId, Result())

    # Tool's CURRENT schema is unchanged -> digest matches -> result returned.
    fake_registry(_SCHEMA_V1)
    with _as("tenant-a", "alice"):
        result = await store.get_result(task.taskId)
    assert result is not None


async def test_get_result_fails_closed_on_digest_drift(
    store: GovernedTaskStore,
    fake_registry: Any,
) -> None:
    pinned = compute_tool_digest(_SCHEMA_V1).sha256
    with _as("tenant-a", "alice"), _current_tool(pinned):
        task = await store.create_task(TaskMetadata(ttl=60_000))
        await store.store_result(task.taskId, Result())

    # Tool's schema drifted since task creation -> fail closed (no result).
    fake_registry(_SCHEMA_V2)
    with _as("tenant-a", "alice"):
        with pytest.raises(McpError, match="tool digest drifted"):
            await store.get_result(task.taskId)


async def test_get_result_fails_closed_when_tool_unresolvable(
    store: GovernedTaskStore,
    fake_registry: Any,
) -> None:
    pinned = compute_tool_digest(_SCHEMA_V1).sha256
    with _as("tenant-a", "alice"), _current_tool(pinned):
        task = await store.create_task(TaskMetadata(ttl=60_000))
        await store.store_result(task.taskId, Result())

    # Tool no longer resolvable -> cannot verify -> fail closed.
    fake_registry(None)
    with _as("tenant-a", "alice"):
        with pytest.raises(McpError, match="tool digest drifted"):
            await store.get_result(task.taskId)


async def test_no_pin_in_context_creates_and_reads_normally(
    store: GovernedTaskStore,
    fake_registry: Any,
) -> None:
    # No current-tool pin bound -> no digest binding, get_result unaffected.
    with _as("tenant-a", "alice"):
        task = await store.create_task(TaskMetadata(ttl=60_000))
        await store.store_result(task.taskId, Result())

    assert store.digest_guard.verify(task.taskId, "0" * 64) is False
    # Even if the tool schema would "drift", an unpinned task is never gated.
    fake_registry(_SCHEMA_V2)
    with _as("tenant-a", "alice"):
        result = await store.get_result(task.taskId)
    assert result is not None
