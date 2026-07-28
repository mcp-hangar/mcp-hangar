"""Consent gating on the inbound ``tasks/update`` (ADR-014 Phase 4, #322).

An inbound ``tasks/update`` IS the client's consent to provide a task's
mid-flight input, so this is where consent is governed. Two properties matter
and neither is visible from the wire shape, which is why they are tested
directly against a spy gate:

* the gate is opened and then ANSWERED (consumed) exactly once on a clean relay;
* a transient upstream refusal DISCARDS the gate without consuming it, leaving
  the task live so a retry can complete -- a failed relay must not burn the
  consent or kill the task.

This module used to test something else. It guarded ``HAS_LIST_TASKS`` /
``HAS_TASKS_UPDATE``, two capability probes meant to track the SDK "as later
betas land the final shape". They never could: they watched ``mcp_types``, which
carries the frozen SEP-1686 generation rather than the SEP-2663 extension, so
``tasks/list`` was always served and ``tasks/update`` never was (ADR-015). Those
guard tests asserted that a latch which cannot trip trips correctly, and went
with the flags.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar._sdk_compat import HAS_NATIVE_TASKS

pytestmark = pytest.mark.skipif(not HAS_NATIVE_TASKS, reason="v2-native Tasks SDK required")

from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore  # noqa: E402
from mcp_hangar.context import identity_context_var  # noqa: E402
from mcp_hangar.domain.events import TaskConsentDecided  # noqa: E402
from mcp_hangar.domain.services.task_consent import TaskConsentGate  # noqa: E402
from mcp_hangar.domain.services.task_ownership import TaskOwner  # noqa: E402
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext  # noqa: E402
from mcp_hangar.domain.value_objects.security import PrincipalType  # noqa: E402
from mcp_hangar.fastmcp_server.task_relay_handlers import register_task_relay_handlers  # noqa: E402
from mcp_hangar.tasks_wire import EXTENSION_ID, EmptyResult  # noqa: E402


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


def _ctx(*, task_id: str = "T1") -> Any:
    """A fake ctx the SEP-2663 refusal ladder admits.

    Modern version, extension declared, ``Mcp-Name`` matching the body -- so a
    test here fails on consent behaviour rather than on the gate. The ladder
    itself is covered in `test_task_relay_handlers`.
    """
    principal = _principal("alice", "tenant-a")
    session = SimpleNamespace(
        protocol_version="2026-07-28",
        client_params=SimpleNamespace(capabilities=SimpleNamespace(extensions={EXTENSION_ID: {}})),
    )
    return SimpleNamespace(
        session=session,
        request=SimpleNamespace(
            state=SimpleNamespace(auth=SimpleNamespace(principal=principal)),
            headers={"mcp-name": task_id},
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


def _handlers(store: GovernedTaskStore, gate: TaskConsentGate, router: _FakeRouter) -> dict[str, tuple[Any, Any]]:
    low = _FakeLow()
    mcp = SimpleNamespace(_mcp_server=low)
    register_task_relay_handlers(mcp, store, gate, router)
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


def test_update_grants_consent_relays_payload_and_records(store: GovernedTaskStore, events: list[object]) -> None:
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
    handlers = _handlers(store, gate, router)
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
    # SEP-2663: the acknowledgement is empty. The re-sync is still observable --
    # on the ledger, which is what governance acts on.
    assert isinstance(result, EmptyResult)
    with _as("tenant-a", "alice"):
        assert store.get_task(("S1", "T1")).status == "completed"


def test_update_transient_relay_failure_discards_gate_and_does_not_fail_task(
    store: GovernedTaskStore, events: list[object]
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
    handlers = _handlers(store, gate, router)

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
