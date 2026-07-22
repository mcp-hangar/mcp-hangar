"""Forward-compat guard tests for the 2026-07-28 (SEP-2663) Tasks-extension deltas.

``mcp==2.0.0b2`` is the 2026-07-28 RC beta: it still ships ``ListTasksResult``
(``tasks/list`` present) and lacks ``UpdateTaskRequest`` (``tasks/update`` absent).
The relay tracks the surface via two static flags -- ``HAS_LIST_TASKS`` /
``HAS_TASKS_UPDATE`` (``_sdk_compat``) -- so that a later beta removing
``tasks/list`` and adding ``tasks/update`` flips the served + advertised surface
WITHOUT a version bump. These tests pin that the guards flip correctly by
monkeypatching the flags in both directions, and exercise the governed inbound
``_update`` handler + the modern-session ``_get`` pass-through directly.

The whole module is skipped off the v2-native Tasks SDK (nothing to guard there).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from mcp_hangar._sdk_compat import HAS_NATIVE_TASKS, GetTaskRequestParams, lowlevel_server

pytestmark = pytest.mark.skipif(not HAS_NATIVE_TASKS, reason="v2-native Tasks SDK required")

from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore  # noqa: E402
from mcp_hangar.context import identity_context_var  # noqa: E402
from mcp_hangar.domain.events import TaskConsentDecided  # noqa: E402
from mcp_hangar.domain.services.task_consent import TaskConsentGate  # noqa: E402
from mcp_hangar.domain.services.task_ownership import TaskOwner  # noqa: E402
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext  # noqa: E402
from mcp_hangar.domain.value_objects.security import PrincipalType  # noqa: E402
from mcp_hangar.fastmcp_server import HangarFunctions, MCPServerFactory, ServerConfig  # noqa: E402
from mcp_hangar.fastmcp_server import task_relay_handlers as trh  # noqa: E402
from mcp_hangar.server.context import reset_context  # noqa: E402

_TASK_METHODS_CORE = ("tasks/get", "tasks/result", "tasks/cancel")


# ---------------------------------------------------------------------------
# Factory-built server harness (mirrors test_fastmcp_server.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_registry() -> HangarFunctions:
    return HangarFunctions(
        list=Mock(return_value={"mcp_servers": []}),
        start=Mock(return_value={"status": "started"}),
        stop=Mock(return_value={"status": "stopped"}),
        invoke=Mock(return_value={"result": 42}),
        tools=Mock(return_value={"tools": []}),
        details=Mock(return_value={"mcp_server": "test"}),
        health=Mock(return_value={"status": "healthy"}),
    )


@pytest.fixture
def _reset_ctx() -> Iterator[None]:
    """Isolate the singleton ApplicationContext around task-relay wiring."""
    reset_context()
    yield
    reset_context()


def _server_low(mock_registry: HangarFunctions) -> Any:
    factory = MCPServerFactory(mock_registry, config=ServerConfig(relay_tasks_enabled=True))
    return lowlevel_server(factory.create_server())


# ---------------------------------------------------------------------------
# 1. tasks/list SERVE guard
# ---------------------------------------------------------------------------


def test_tasks_list_serve_guard_off(mock_registry: HangarFunctions, _reset_ctx: None, monkeypatch: Any) -> None:
    """With ``HAS_LIST_TASKS`` False the ``tasks/list`` handler is not registered,
    while the three always-on handlers remain."""
    monkeypatch.setattr(trh, "HAS_LIST_TASKS", False)
    low = _server_low(mock_registry)
    assert low.get_request_handler("tasks/list") is None
    for method in _TASK_METHODS_CORE:
        assert low.get_request_handler(method) is not None, method


# ---------------------------------------------------------------------------
# 2. tasks/list ADVERTISE guard
# ---------------------------------------------------------------------------


def test_tasks_list_advertise_guard_off(mock_registry: HangarFunctions, _reset_ctx: None, monkeypatch: Any) -> None:
    """With ``HAS_LIST_TASKS`` False (read by the factory at call time) the advertised
    ``tasks.list`` capability is dropped; ``cancel`` / ``requests`` stay set."""
    monkeypatch.setattr("mcp_hangar._sdk_compat.HAS_LIST_TASKS", False)
    low = _server_low(mock_registry)
    tasks = low.get_capabilities().tasks
    assert tasks is not None
    assert tasks.list is None
    assert tasks.cancel is not None
    assert tasks.requests is not None


# ---------------------------------------------------------------------------
# 3. b2 default parity (real flags, no patch)
# ---------------------------------------------------------------------------


def test_b2_default_list_present_update_absent(mock_registry: HangarFunctions, _reset_ctx: None) -> None:
    """On the b2 RC beta (real flags) ``tasks/list`` IS served + advertised and
    the inbound ``tasks/update`` handler is NOT registered."""
    assert trh.HAS_LIST_TASKS is True
    assert trh.HAS_TASKS_UPDATE is False
    low = _server_low(mock_registry)
    assert low.get_request_handler("tasks/list") is not None
    assert low.get_capabilities().tasks.list is not None
    assert low.get_request_handler("tasks/update") is None


# ---------------------------------------------------------------------------
# 4. tasks/update REGISTER guard
# ---------------------------------------------------------------------------


def test_tasks_update_register_guard_on(mock_registry: HangarFunctions, _reset_ctx: None, monkeypatch: Any) -> None:
    """With ``HAS_TASKS_UPDATE`` True (and a real param model) the inbound
    ``tasks/update`` handler is registered."""
    monkeypatch.setattr(trh, "HAS_TASKS_UPDATE", True)
    # b2 has no ``UpdateTaskRequestParams``; a real request-params type stands in for
    # ``add_request_handler``'s type slot.
    monkeypatch.setattr(trh, "UpdateTaskRequestParams", GetTaskRequestParams)
    low = _server_low(mock_registry)
    assert low.get_request_handler("tasks/update") is not None


# ---------------------------------------------------------------------------
# Direct-drive harness for the governed handlers (mirrors test_task_consent_flow.py)
# ---------------------------------------------------------------------------


class _FakeLow:
    def __init__(self) -> None:
        self.handlers: dict[str, tuple[Any, Any]] = {}

    def add_request_handler(self, method: str, params_type: Any, handler: Any) -> None:
        self.handlers[method] = (params_type, handler)


class _FakeRouter:
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], float]] = []
        self.responses = responses or {}

    def __call__(self, target_server_id: str, method: str, params: dict[str, Any], timeout: float) -> Any:
        self.calls.append((target_server_id, method, params, timeout))
        value = self.responses.get(method)
        return value() if callable(value) else value

    def methods(self) -> list[str]:
        return [c[1] for c in self.calls]


@contextmanager
def _as(tenant_id: str | None, principal_id: str | None = None) -> Iterator[None]:
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
    return SimpleNamespace(
        is_anonymous=lambda: False,
        id=SimpleNamespace(value=user_id),
        type=PrincipalType.USER,
        tenant_id=tenant_id,
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


def _ctx(*, order: list[str] | None = None, protocol_version: str = "2025-11-25") -> Any:
    """A fake ctx with a bridged principal + a downstream elicitation session."""
    principal = _principal("alice", "tenant-a")
    elic = SimpleNamespace(form=SimpleNamespace(), url=None)
    client_params = SimpleNamespace(capabilities=SimpleNamespace(elicitation=elic))

    async def elicit_form(message: str, schema: Any, related_request_id: Any = None) -> Any:
        if order is not None:
            order.append("elicit")
        from mcp_types import ElicitResult

        return ElicitResult(action="decline")  # type: ignore[arg-type]

    session = SimpleNamespace(
        client_params=client_params,
        protocol_version=protocol_version,
        elicit_form=elicit_form,
    )
    return SimpleNamespace(
        session=session,
        request_context=SimpleNamespace(
            request=SimpleNamespace(state=SimpleNamespace(auth=SimpleNamespace(principal=principal)))
        ),
    )


def _spy_gate(order: list[str]) -> TaskConsentGate:
    gate = TaskConsentGate()
    for name in ("open", "answer", "discard"):
        real = getattr(gate, name)

        def wrap(real: Any = real, name: str = name) -> Any:
            def inner(*a: Any, **k: Any) -> Any:
                order.append(name)
                return real(*a, **k)

            return inner

        setattr(gate, name, wrap())
    return gate


def _register(store: GovernedTaskStore, server: str, task_id: str, tenant: str, principal: str) -> None:
    with _as(tenant, principal):
        task = store.mint_from_upstream(_upstream_task(task_id))
        store.register_relayed_task(target_server_id=server, task=task, expected_owner=TaskOwner(tenant, principal))


def _handlers(
    store: GovernedTaskStore, gate: TaskConsentGate, router: _FakeRouter, monkeypatch: Any, *, with_update: bool = False
) -> dict[str, tuple[Any, Any]]:
    if with_update:
        monkeypatch.setattr(trh, "HAS_TASKS_UPDATE", True)
        monkeypatch.setattr(trh, "UpdateTaskRequestParams", GetTaskRequestParams)
    low = _FakeLow()
    mcp = SimpleNamespace(_mcp_server=low)
    trh.register_task_relay_handlers(mcp, store, gate, router)
    return low.handlers


def _update_params(task_id: str, payload: dict[str, Any]) -> Any:
    return SimpleNamespace(task_id=task_id, model_dump=lambda **_: payload)


@pytest.fixture
def events() -> list[object]:
    return []


@pytest.fixture
def store(events: list[object]) -> GovernedTaskStore:
    return GovernedTaskStore(event_publisher=events.append)


# ---------------------------------------------------------------------------
# 5. governed inbound ``_update`` behavior
# ---------------------------------------------------------------------------


def test_update_grants_consent_relays_payload_and_records(
    store: GovernedTaskStore, events: list[object], monkeypatch: Any
) -> None:
    """A clean inbound ``tasks/update``: authorize -> open+answer gate -> relay the
    client payload upstream -> record the grant -> re-sync -> flat snapshot."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    order: list[str] = []
    gate = _spy_gate(order)
    router = _FakeRouter(
        {
            "tasks/get": {"result": _upstream_task("T1", status="input_required")},
            "tasks/update": {"result": _upstream_task("T1", status="completed")},
        }
    )
    handlers = _handlers(store, gate, router, monkeypatch, with_update=True)
    payload = {"task_id": "T1", "input": {"token": "sekret"}}

    result = asyncio.run(handlers["tasks/update"][1](_ctx(), _update_params("T1", payload)))

    # Gate opened then answered (consumed) exactly once.
    assert order == ["open", "answer"]
    # Client payload relayed upstream verbatim.
    update_calls = [c for c in router.calls if c[1] == "tasks/update"]
    assert len(update_calls) == 1
    assert update_calls[0][0] == "S1" and update_calls[0][2] == payload
    # Positive decision recorded in provenance.
    decided = [e for e in events if isinstance(e, TaskConsentDecided)]
    assert len(decided) == 1 and decided[0].granted is True
    # Returns a re-synced snapshot.
    assert result.model_dump(by_alias=True)["status"] == "completed"


def test_update_transient_relay_failure_discards_gate_and_does_not_fail_task(
    store: GovernedTaskStore, events: list[object], monkeypatch: Any
) -> None:
    """A transient upstream ``tasks/update`` error discards the gate WITHOUT answering,
    raises, records no decision, and leaves the task live (not failed)."""
    from mcp_hangar._sdk_compat import McpError

    _register(store, "S1", "T1", "tenant-a", "alice")
    order: list[str] = []
    gate = _spy_gate(order)
    router = _FakeRouter(
        {
            "tasks/get": {"result": _upstream_task("T1", status="input_required")},
            "tasks/update": {"error": {"code": -32000, "message": "temporarily unavailable"}},
        }
    )
    handlers = _handlers(store, gate, router, monkeypatch, with_update=True)

    with pytest.raises(McpError) as exc:
        asyncio.run(handlers["tasks/update"][1](_ctx(), _update_params("T1", {"task_id": "T1"})))
    assert "retry" in str(exc.value)

    # Gate discarded, never answered (consent not consumed).
    assert order == ["open", "discard"]
    assert [e for e in events if isinstance(e, TaskConsentDecided)] == []
    # Task NOT failed -- recoverable.
    with _as("tenant-a", "alice"):
        snap = store.get_task(("S1", "T1"))
    assert snap is not None and snap.status != "failed"


# ---------------------------------------------------------------------------
# 6. modern-session ``_get`` pass-through (no synchronous elicit)
# ---------------------------------------------------------------------------


def test_modern_session_get_passes_input_required_through_without_elicit(
    store: GovernedTaskStore, monkeypatch: Any
) -> None:
    """On a 2026-07-28 session an ``input_required`` ``tasks/get`` is passed through
    flat WITHOUT ever calling ``elicit_form`` (the client comes back via ``_update``)."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    order: list[str] = []
    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1", status="input_required")}})
    handlers = _handlers(store, TaskConsentGate(), router, monkeypatch)

    result = asyncio.run(
        handlers["tasks/get"][1](_ctx(order=order, protocol_version="2026-07-28"), SimpleNamespace(task_id="T1"))
    )

    # Modern branch: no synchronous elicit; the input_required snapshot passes through.
    assert "elicit" not in order
    assert result.model_dump(by_alias=True)["status"] == "input_required"


def test_legacy_session_get_does_elicit_on_input_required(store: GovernedTaskStore, monkeypatch: Any) -> None:
    """Contrast: a 2025-11-25 session DOES call ``elicit_form`` synchronously (proving
    the branch is version-gated)."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    order: list[str] = []
    router = _FakeRouter(
        {
            "tasks/get": {"result": _upstream_task("T1", status="input_required")},
            "tasks/cancel": {"result": _upstream_task("T1", status="cancelled")},
        }
    )
    handlers = _handlers(store, TaskConsentGate(), router, monkeypatch)

    asyncio.run(
        handlers["tasks/get"][1](_ctx(order=order, protocol_version="2025-11-25"), SimpleNamespace(task_id="T1"))
    )

    assert "elicit" in order
