"""Consent-gate invariants that do not depend on how consent is collected.

The rest of this module used to test the SYNCHRONOUS 2025-11-25 consent flow:
an ``input_required`` task was resolved inside ``tasks/get`` by eliciting the
downstream client via ``ctx.session.elicit_form``. That flow is gone, because
the wire it belonged to is gone -- Hangar no longer serves ``tasks/*`` on
2025-11-25 (ADR-015), and on the SEP-2663 wire the client resolves its own input
by driving ``tasks/update``, which is governed in `test_task_update_consent`.

Deleting tests alongside the code they covered is right, but it is worth being
explicit about what left with them: the decline / cancel / no-back-channel /
elicit-error matrix, and the concurrent-reprompt guard. Those guarded an
interactive prompt Hangar no longer issues. What did NOT leave is everything
below -- these two invariants are properties of the gate and the consent key
themselves, so they outlive any particular way of asking.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.events import TaskFailed
from mcp_hangar.domain.services.task_consent import TaskConsentGate
from mcp_hangar.domain.services.task_ownership import TaskOwner
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.security import PrincipalType
from mcp_hangar.fastmcp_server.task_relay_handlers import (
    _derive_input_key,
    register_task_relay_handlers,
)
from mcp_types import ElicitResult

# ---------------------------------------------------------------------------
# Fakes + helpers
# ---------------------------------------------------------------------------


class _FakeLow:
    def __init__(self) -> None:
        self.handlers: dict[str, tuple[Any, Any]] = {}

    def add_request_handler(self, method: str, params_type: Any, handler: Any) -> None:
        self.handlers[method] = (params_type, handler)


class _FakeRouter:
    """Injected upstream router returning canned (optionally stateful) responses."""

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


def _consent_ctx(
    user_id: str | None = "alice",
    tenant_id: str | None = "tenant-a",
    *,
    order: list[str] | None = None,
    elicitation: bool = True,
    action: str = "accept",
    error: BaseException | None = None,
    protocol_version: str = "2025-11-25",
) -> Any:
    """A fake ctx with a bridged principal AND a downstream elicitation session."""
    principal = _principal(user_id, tenant_id) if user_id else None
    elic = SimpleNamespace(form=SimpleNamespace(), url=None) if elicitation else None
    client_params = SimpleNamespace(capabilities=SimpleNamespace(elicitation=elic))

    async def elicit_form(message: str, schema: Any, related_request_id: Any = None) -> ElicitResult:
        if order is not None:
            order.append("elicit")
        if error is not None:
            raise error
        return ElicitResult(action=action)  # type: ignore[arg-type]

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


def _spy_gate(order: list[str] | None = None, **kw: Any) -> TaskConsentGate:
    """A real gate whose open/answer/discard append their name to ``order``."""
    gate = TaskConsentGate(**kw)
    log = order if order is not None else []
    for name in ("open", "answer", "discard"):
        real = getattr(gate, name)

        def wrap(real: Any = real, name: str = name) -> Any:
            def inner(*a: Any, **k: Any) -> Any:
                log.append(name)
                return real(*a, **k)

            return inner

        setattr(gate, name, wrap())
    return gate


def _register(store: GovernedTaskStore, server: str, task_id: str, tenant: str, principal: str) -> None:
    with _as(tenant, principal):
        task = store.mint_from_upstream(_upstream_task(task_id))
        store.register_relayed_task(target_server_id=server, task=task, expected_owner=TaskOwner(tenant, principal))


def _register_corr(
    store: GovernedTaskStore, server: str, task_id: str, tenant: str, principal: str, correlation_id: str
) -> None:
    with _as(tenant, principal):
        task = store.mint_from_upstream(_upstream_task(task_id))
        store.relay_and_govern(
            target_server_id=server,
            task=task,
            expected_owner=TaskOwner(tenant, principal),
            correlation_id=correlation_id,
        )


def _handlers(store: GovernedTaskStore, gate: TaskConsentGate, router: _FakeRouter) -> dict[str, tuple[Any, Any]]:
    low = _FakeLow()
    mcp = SimpleNamespace(_mcp_server=low)
    register_task_relay_handlers(mcp, store, gate, router)
    return low.handlers


def _get(handlers: dict[str, tuple[Any, Any]], ctx: Any, task_id: str = "T1") -> Any:
    return asyncio.run(handlers["tasks/get"][1](ctx, SimpleNamespace(task_id=task_id)))


@pytest.fixture
def events() -> list[object]:
    return []


@pytest.fixture
def store(events: list[object]) -> GovernedTaskStore:
    return GovernedTaskStore(event_publisher=events.append)


# ---------------------------------------------------------------------------
# elicit-first / no pre-decision race (finding #1)
# ---------------------------------------------------------------------------


def test_evicted_live_consent_fails_task_closed(store: GovernedTaskStore, events: list[object]) -> None:
    """A live pending consent evicted by the gate cap fails the task (finding #16)."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    _register(store, "S1", "T2", "tenant-a", "alice")
    # Wire the gate exactly as the factory does, with a cap of 1 to force eviction.
    gate = TaskConsentGate(maxsize=1, on_evict=lambda ck: store.fail_task((ck[0], ck[1]), "consent_unavailable"))

    gate.open(("S1", "T1"), "ik-1")
    gate.open(("S1", "T2"), "ik-2")  # evicts T1 -> on_evict fails T1 closed

    with _as("tenant-a", "alice"):
        snap = store.get_task(("S1", "T1"))
    assert snap is not None and snap.status == "failed"
    failed = [e for e in events if isinstance(e, TaskFailed) and e.task_id == "T1"]
    assert len(failed) == 1 and failed[0].error_type == "consent_unavailable"


def test_derive_input_key_is_deterministic_and_nonempty() -> None:
    a = _derive_input_key(_upstream_task("T1", status="input_required", statusMessage="need token"))
    b = _derive_input_key(_upstream_task("T1", status="input_required", statusMessage="need token"))
    c = _derive_input_key({"inputRequests": {"z": {}, "a": {}}})
    d = _derive_input_key({"inputRequests": {"a": {}, "z": {}}})
    assert a == b and a  # deterministic + non-empty
    assert c == d  # stable ordering over the request-id set
    assert a != c
