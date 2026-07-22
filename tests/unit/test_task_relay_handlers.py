"""Unit tests for the relayed-task serving surface (ADR-014, Phase 2).

Exercises the four ``tasks/*`` request handlers registered by
:func:`register_task_relay_handlers` against a REAL :class:`GovernedTaskStore`, a
fake injected upstream router, and a fake FastMCP request context that carries an
authenticated principal (the streamable-HTTP identity bridge). Handlers are
invoked directly.

Invariants under test: flat (non-nested) result construction, upstream-truthful
snapshot sync, once-only ``TaskCompleted``/``TaskCancelled`` emission, cancel
truthfulness, cross-tenant denial with no existence leak, ``tasks/result``
reconstruction + digest-drift propagation, and that NO ``tasks/update`` handler
is registered.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar._sdk_compat import (
    CallToolResult,
    CancelTaskRequestParams,
    CancelTaskResult,
    GetTaskPayloadRequestParams,
    GetTaskRequestParams,
    GetTaskResult,
    McpError,
    PaginatedRequestParams,
)
from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.events import TaskCancelled, TaskCompleted
from mcp_hangar.domain.services.task_consent import TaskConsentGate
from mcp_hangar.domain.services.task_ownership import TaskOwner
from mcp_hangar.domain.value_objects.security import PrincipalType
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.fastmcp_server.task_relay_handlers import (
    _cancel_confirmed,
    register_task_relay_handlers,
)

# ---------------------------------------------------------------------------
# Fakes + helpers
# ---------------------------------------------------------------------------


class _FakeLow:
    """Records ``add_request_handler`` registrations from the low-level server."""

    def __init__(self) -> None:
        self.handlers: dict[str, tuple[Any, Any]] = {}

    def add_request_handler(self, method: str, params_type: Any, handler: Any) -> None:
        self.handlers[method] = (params_type, handler)


class _FakeRouter:
    """Injected upstream router returning canned per-method responses."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], float]] = []
        self.responses = responses or {}

    def __call__(self, target_server_id: str, method: str, params: dict[str, Any], timeout: float) -> Any:
        self.calls.append((target_server_id, method, params, timeout))
        value = self.responses.get(method)
        return value() if callable(value) else value


@contextmanager
def _as(tenant_id: str | None, principal_id: str | None = None) -> Iterator[None]:
    """Bind the identity contextvar for a setup block (task registration)."""
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


def _principal(user_id: str, tenant_id: str) -> Any:
    """A fake auth Principal matching what ``_principal_to_identity_context`` reads."""
    return SimpleNamespace(
        is_anonymous=lambda: False,
        id=SimpleNamespace(value=user_id),
        type=PrincipalType.USER,
        tenant_id=tenant_id,
    )


def _ctx(user_id: str | None = None, tenant_id: str | None = None) -> Any:
    """A fake FastMCP ctx exposing ``request_context.request.state.auth.principal``."""
    principal = _principal(user_id, tenant_id) if user_id else None
    return SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(state=SimpleNamespace(auth=SimpleNamespace(principal=principal)))
        )
    )


def _upstream_task(task_id: str, *, status: str = "working", **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "taskId": task_id,
        "status": status,
        "createdAt": "2020-01-01T00:00:00Z",
        "lastUpdatedAt": "2020-01-01T00:00:00Z",
        "ttl": 60_000,
    }
    data.update(extra)
    return data


def _register(store: GovernedTaskStore, server: str, task_id: str, tenant: str, principal: str) -> None:
    with _as(tenant, principal):
        task = store.mint_from_upstream(_upstream_task(task_id))
        store.register_relayed_task(target_server_id=server, task=task, expected_owner=TaskOwner(tenant, principal))


def _handlers(
    store: GovernedTaskStore, router: _FakeRouter, gate: TaskConsentGate | None = None
) -> dict[str, tuple[Any, Any]]:
    low = _FakeLow()
    mcp = SimpleNamespace(_mcp_server=low)
    register_task_relay_handlers(mcp, store, gate or TaskConsentGate(), router)
    return low.handlers


@pytest.fixture
def events() -> list[object]:
    return []


@pytest.fixture
def store(events: list[object]) -> GovernedTaskStore:
    return GovernedTaskStore(event_publisher=events.append)


# ---------------------------------------------------------------------------
# Flat-result round-trip (anti-pattern lockout)
# ---------------------------------------------------------------------------


def test_flat_get_result_spread_dumps_camelcase_wire_json() -> None:
    snapshot = {
        "task_id": "T1",
        "status": "completed",
        "created_at": "2020-01-01T00:00:00Z",
        "last_updated_at": "2020-01-02T00:00:00Z",
        "ttl": 60_000,
    }
    result = GetTaskResult(**snapshot)
    wire = result.model_dump(by_alias=True)
    assert wire["taskId"] == "T1"
    assert wire["status"] == "completed"
    assert wire["createdAt"] == "2020-01-01T00:00:00Z"
    assert wire["lastUpdatedAt"] == "2020-01-02T00:00:00Z"


def test_flat_cancel_result_spread_dumps_camelcase_wire_json() -> None:
    result = CancelTaskResult(
        task_id="T1",
        status="cancelled",
        created_at="2020-01-01T00:00:00Z",
        last_updated_at="2020-01-02T00:00:00Z",
        ttl=None,
    )
    wire = result.model_dump(by_alias=True)
    assert wire["taskId"] == "T1"
    assert wire["status"] == "cancelled"


def test_nested_task_form_is_locked_out() -> None:
    """The anti-pattern ``GetTaskResult(task=...)`` must raise (results are FLAT)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GetTaskResult(task={"taskId": "T1", "status": "working"})  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Registration surface
# ---------------------------------------------------------------------------


def test_registers_four_handlers_with_exact_methods_and_param_types(store: GovernedTaskStore) -> None:
    handlers = _handlers(store, _FakeRouter())
    assert set(handlers) == {"tasks/get", "tasks/result", "tasks/cancel", "tasks/list"}
    assert handlers["tasks/get"][0] is GetTaskRequestParams
    assert handlers["tasks/result"][0] is GetTaskPayloadRequestParams
    assert handlers["tasks/cancel"][0] is CancelTaskRequestParams
    assert handlers["tasks/list"][0] is PaginatedRequestParams


def test_no_tasks_update_handler_registered(store: GovernedTaskStore) -> None:
    handlers = _handlers(store, _FakeRouter())
    assert "tasks/update" not in handlers


# ---------------------------------------------------------------------------
# tasks/get
# ---------------------------------------------------------------------------


def test_get_relays_to_owning_server_updates_snapshot_returns_flat(
    store: GovernedTaskStore,
) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1", status="working", statusMessage="crunching")}})
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    # Relayed to the RIGHT upstream server, verbatim task_id param.
    assert router.calls == [("S1", "tasks/get", {"task_id": "T1"}, 30.0)]
    assert isinstance(result, GetTaskResult)
    wire = result.model_dump(by_alias=True)
    assert wire["taskId"] == "T1"
    assert wire["statusMessage"] == "crunching"


def test_get_upstream_error_returns_local_snapshot_unchanged(store: GovernedTaskStore) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/get": {"error": {"code": -32000, "message": "boom"}}})
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))
    assert result.model_dump(by_alias=True)["status"] == "working"  # unchanged, not fabricated


def test_get_emits_task_completed_once_on_working_to_completed(store: GovernedTaskStore, events: list[object]) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1", status="completed")}})
    handlers = _handlers(store, router)
    get = handlers["tasks/get"][1]
    ctx = _ctx("alice", "tenant-a")

    r1 = asyncio.run(get(ctx, SimpleNamespace(task_id="T1")))
    r2 = asyncio.run(get(ctx, SimpleNamespace(task_id="T1")))  # repeated poll

    assert r1.model_dump(by_alias=True)["status"] == "completed"
    assert r2.model_dump(by_alias=True)["status"] == "completed"
    completed = [e for e in events if isinstance(e, TaskCompleted)]
    assert len(completed) == 1  # deduped across polls
    assert completed[0].task_id == "T1"
    assert completed[0].tenant_id == "tenant-a"


def test_get_input_required_without_elicitation_channel_fails_closed(store: GovernedTaskStore) -> None:
    """No downstream elicitation channel (ctx has no session) -> fail-closed, never left input_required."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1", status="input_required")}})
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))
    assert result.model_dump(by_alias=True)["status"] == "failed"


# ---------------------------------------------------------------------------
# tasks/list
# ---------------------------------------------------------------------------


def test_list_returns_only_callers_tasks_no_cursor(store: GovernedTaskStore) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    _register(store, "S2", "T2", "tenant-a", "alice")
    _register(store, "S3", "T3", "tenant-b", "bob")
    handlers = _handlers(store, _FakeRouter())

    result = asyncio.run(handlers["tasks/list"][1](_ctx("alice", "tenant-a"), SimpleNamespace()))
    ids = {t.task_id for t in result.tasks}
    assert ids == {"T1", "T2"}
    assert result.model_dump(by_alias=True).get("nextCursor") is None


# ---------------------------------------------------------------------------
# Cross-tenant / ownership (no existence leak)
# ---------------------------------------------------------------------------


def test_get_cross_tenant_denied_no_leak_and_no_upstream_call(store: GovernedTaskStore) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1")}})
    handlers = _handlers(store, router)

    with pytest.raises(McpError) as exc:
        asyncio.run(handlers["tasks/get"][1](_ctx("bob", "tenant-b"), SimpleNamespace(task_id="T1")))
    assert "Task not found: T1" in str(exc.value)
    assert router.calls == []  # never relayed for a non-owned task


def test_get_unknown_task_id_denied(store: GovernedTaskStore) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    handlers = _handlers(store, _FakeRouter())
    with pytest.raises(McpError) as exc:
        asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="NOPE")))
    assert "Task not found: NOPE" in str(exc.value)


# ---------------------------------------------------------------------------
# tasks/cancel truthfulness
# ---------------------------------------------------------------------------


def test_cancel_confirmed_retires_and_emits_once(store: GovernedTaskStore, events: list[object]) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/cancel": {"result": _upstream_task("T1", status="cancelled")}})
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/cancel"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    assert isinstance(result, CancelTaskResult)
    assert result.model_dump(by_alias=True)["status"] == "cancelled"
    cancelled = [e for e in events if isinstance(e, TaskCancelled)]
    assert len(cancelled) == 1
    assert cancelled[0].task_id == "T1"
    # Entry retired: a follow-up cancel now denies (not found), no double event.
    with pytest.raises(McpError):
        asyncio.run(handlers["tasks/cancel"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))
    assert len([e for e in events if isinstance(e, TaskCancelled)]) == 1


def test_cancel_upstream_error_keeps_entry_returns_true_status_no_event(
    store: GovernedTaskStore, events: list[object]
) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/cancel": {"error": {"code": -32000, "message": "refused"}}})
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/cancel"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    # True current status returned (working), NOT a fabricated 'cancelled'.
    assert result.model_dump(by_alias=True)["status"] == "working"
    assert [e for e in events if isinstance(e, TaskCancelled)] == []
    # Entry KEPT: still resolvable via list.
    listed = asyncio.run(handlers["tasks/list"][1](_ctx("alice", "tenant-a"), SimpleNamespace()))
    assert {t.task_id for t in listed.tasks} == {"T1"}


def test_cancel_upstream_reports_non_cancelled_status_keeps_entry(
    store: GovernedTaskStore, events: list[object]
) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/cancel": {"result": _upstream_task("T1", status="working")}})
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/cancel"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))
    assert result.model_dump(by_alias=True)["status"] == "working"
    assert [e for e in events if isinstance(e, TaskCancelled)] == []


def test_cancel_confirmed_predicate() -> None:
    assert _cancel_confirmed({"result": {"status": "cancelled"}}) is True
    assert _cancel_confirmed({"result": {}}) is True  # clean result, no contradicting status
    assert _cancel_confirmed({"result": None}) is False
    assert _cancel_confirmed({"error": {"code": -1}}) is False
    assert _cancel_confirmed({"result": {"status": "working"}}) is False
    assert _cancel_confirmed("nonsense") is False


# ---------------------------------------------------------------------------
# tasks/result
# ---------------------------------------------------------------------------


def test_result_reconstructs_call_tool_result(store: GovernedTaskStore) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    payload = {"content": [{"type": "text", "text": "done"}], "isError": False}
    router = _FakeRouter({"tasks/result": {"result": payload}})
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/result"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))
    assert isinstance(result, CallToolResult)
    assert router.calls == [("S1", "tasks/result", {"task_id": "T1"}, 30.0)]
    assert result.content[0].text == "done"


def test_result_upstream_error_raises(store: GovernedTaskStore) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/result": {"error": {"code": -32000, "message": "boom"}}})
    handlers = _handlers(store, router)
    with pytest.raises(McpError) as exc:
        asyncio.run(handlers["tasks/result"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))
    assert "task result unavailable" in str(exc.value)


def test_result_digest_drift_propagates_and_skips_upstream(
    store: GovernedTaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_hangar._sdk_compat import INVALID_PARAMS, make_mcp_error

    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/result": {"result": {"content": []}}})

    def _drift(key: Any) -> None:
        raise make_mcp_error(INVALID_PARAMS, "tool digest drifted since task creation")

    monkeypatch.setattr(store, "_verify_pinned_digest", _drift)
    handlers = _handlers(store, router)

    with pytest.raises(McpError) as exc:
        asyncio.run(handlers["tasks/result"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))
    assert "digest drifted" in str(exc.value)
    assert router.calls == []  # drift fails before the relay


# ---------------------------------------------------------------------------
# Identity bridge
# ---------------------------------------------------------------------------


def test_absent_principal_is_unattributed_and_cannot_reach_attributed_task(
    store: GovernedTaskStore,
) -> None:
    """No principal on ctx -> unattributed caller -> cannot see tenant-a's task."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1")}})
    handlers = _handlers(store, router)

    with pytest.raises(McpError):
        asyncio.run(handlers["tasks/get"][1](_ctx(), SimpleNamespace(task_id="T1")))
    assert router.calls == []
