"""Unit tests for the synchronous mid-flight consent flow (ADR-014, Phase 4, #322).

On the 2025-11-25 protocol there is NO inbound ``tasks/update``: when a relayed
task's upstream status is ``input_required``, :func:`register_task_relay_handlers`
resolves it SYNCHRONOUSLY inside ``tasks/get`` -- eliciting the downstream client
for consent via ``ctx.session.elicit_form`` and relaying the answer upstream.

These tests exercise the review's consent matrix against a REAL
:class:`GovernedTaskStore` + :class:`TaskConsentGate`, a fake upstream router, and a
fake ctx whose ``session.elicit_form`` returns a canned :class:`ElicitResult` and
whose ``ClientCapabilities`` can be toggled:

* elicit-first / no race: the gate opens ONLY after an accept (finding #1);
* tenant-above-gate: cross-tenant denial fires at authorize, before any elicit;
* never-hang matrix: decline / cancel / no back-channel / arbitrary elicit error /
  missing elicitation capability / evicted consent all terminally FAIL the task
  (finding #9, D6) -- never left ``input_required``;
* transient-upstream recovery: a transient answer-relay failure leaves consent
  recoverable, not consumed (finding #3);
* the concurrent-reprompt guard (finding #6);
* provenance: an accept records ``TaskConsentDecided(granted=True)``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from mcp.shared.exceptions import NoBackChannelError
import pytest

from mcp_hangar._sdk_compat import McpError
from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.events import TaskConsentDecided, TaskFailed
from mcp_hangar.domain.services.task_consent import TaskConsentGate
from mcp_hangar.domain.services.task_ownership import TaskOwner
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.security import PrincipalType
from mcp_hangar.fastmcp_server.task_relay_handlers import (
    _derive_input_key,
    _is_modern_tasks_session,
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


def test_accept_opens_gate_only_after_elicit_and_relays_answer_once(
    store: GovernedTaskStore, events: list[object]
) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    order: list[str] = []
    gate = _spy_gate(order)
    router = _FakeRouter(
        {
            "tasks/get": iter(
                [
                    {"result": _upstream_task("T1", status="input_required")},
                    {"result": _upstream_task("T1", status="completed")},
                ]
            ).__next__,
            "tasks/update": {"result": {}},
        }
    )
    handlers = _handlers(store, gate, router)

    result = _get(handlers, _consent_ctx(order=order))

    # The gate opened ONLY after the elicitation returned (no pre-decision race).
    assert "open" in order
    assert order.index("elicit") < order.index("open")
    assert order.count("open") == 1
    # The consented answer was relayed upstream exactly once.
    assert router.methods().count("tasks/update") == 1
    # Post-input re-relay reflected the new upstream status.
    assert result.model_dump(by_alias=True)["status"] == "completed"
    # Consent consumed (single-use) and provenance recorded.
    assert order.count("answer") == 1
    decided = [e for e in events if isinstance(e, TaskConsentDecided)]
    assert len(decided) == 1 and decided[0].granted is True


# ---------------------------------------------------------------------------
# tenant-above-gate: authorize denies before any elicitation/gate touch
# ---------------------------------------------------------------------------


def test_cross_tenant_denied_before_any_elicit_or_gate(store: GovernedTaskStore) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    order: list[str] = []
    gate = _spy_gate(order)
    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1", status="input_required")}})
    handlers = _handlers(store, gate, router)

    with pytest.raises(McpError) as exc:
        _get(handlers, _consent_ctx("bob", "tenant-b", order=order))
    assert "Task not found: T1" in str(exc.value)
    # No upstream relay, no elicitation, no gate touch -- the gate is structurally
    # BELOW the tenant authorize chokepoint.
    assert router.calls == []
    assert order == []


# ---------------------------------------------------------------------------
# never-hang matrix (finding #9, D6): every non-accept terminally FAILS the task
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    ["decline", "cancel", "no_back_channel", "arbitrary_error", "no_capability"],
)
def test_never_hang_denial_paths_fail_closed(store: GovernedTaskStore, events: list[object], kind: str) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    order: list[str] = []
    gate = _spy_gate(order)
    router = _FakeRouter(
        {
            "tasks/get": {"result": _upstream_task("T1", status="input_required")},
            "tasks/cancel": {"result": _upstream_task("T1", status="cancelled")},
        }
    )
    handlers = _handlers(store, gate, router)

    if kind == "decline":
        ctx = _consent_ctx(order=order, action="decline")
    elif kind == "cancel":
        ctx = _consent_ctx(order=order, action="cancel")
    elif kind == "no_back_channel":
        ctx = _consent_ctx(order=order, error=NoBackChannelError("elicitation/create"))
    elif kind == "arbitrary_error":
        ctx = _consent_ctx(order=order, error=RuntimeError("client blew up mid-elicit"))
    else:  # no_capability
        ctx = _consent_ctx(order=order, elicitation=False)

    result = _get(handlers, ctx)

    # Terminally failed -- NEVER left input_required (D6 never-hang).
    assert result.model_dump(by_alias=True)["status"] == "failed"
    failed = [e for e in events if isinstance(e, TaskFailed)]
    assert len(failed) == 1 and failed[0].error_type == "consent_denied"
    # Best-effort upstream cancel was relayed.
    assert ("S1", "tasks/cancel", {"task_id": "T1"}, 30.0) in router.calls
    # Gate discarded; negative decision recorded.
    assert "discard" in order
    assert "open" not in order  # gate never opened on a denial
    decided = [e for e in events if isinstance(e, TaskConsentDecided)]
    assert len(decided) == 1 and decided[0].granted is False


def test_denial_upstream_cancel_failure_is_best_effort(store: GovernedTaskStore, events: list[object]) -> None:
    """Even if the best-effort upstream cancel raises, the task still fails closed."""
    _register(store, "S1", "T1", "tenant-a", "alice")

    def _boom() -> Any:
        raise RuntimeError("upstream unreachable")

    router = _FakeRouter(
        {"tasks/get": {"result": _upstream_task("T1", status="input_required")}, "tasks/cancel": _boom}
    )
    handlers = _handlers(store, TaskConsentGate(), router)

    result = _get(handlers, _consent_ctx(action="decline"))
    assert result.model_dump(by_alias=True)["status"] == "failed"
    assert [e for e in events if isinstance(e, TaskFailed)]


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


# ---------------------------------------------------------------------------
# transient-upstream recovery (finding #3): consent not permanently consumed
# ---------------------------------------------------------------------------


def test_transient_answer_relay_failure_is_recoverable(store: GovernedTaskStore, events: list[object]) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    order: list[str] = []
    gate = _spy_gate(order)
    get_seq = iter(
        [
            {"result": _upstream_task("T1", status="input_required")},  # call 1 poll
            {"result": _upstream_task("T1", status="input_required")},  # call 2 retry poll
            {"result": _upstream_task("T1", status="completed")},  # call 2 post-input re-relay
        ]
    )
    update_seq = iter(
        [
            {"error": {"code": -32000, "message": "temporarily unavailable"}},  # transient
            {"result": {}},  # recovered
        ]
    )
    router = _FakeRouter({"tasks/get": get_seq.__next__, "tasks/update": update_seq.__next__})
    handlers = _handlers(store, gate, router)
    ctx = _consent_ctx(order=order)

    # Call 1: accept -> answer relay fails transiently -> recoverable error, NOT consumed.
    with pytest.raises(McpError) as exc:
        _get(handlers, ctx)
    assert "retry" in str(exc.value)
    assert order.count("answer") == 0  # consent NOT consumed
    assert gate.is_consent_pending(("S1", "T1"), _derive_input_key(_upstream_task("T1"))) is False  # discarded
    with _as("tenant-a", "alice"):
        assert store.get_task(("S1", "T1")).status == "input_required"  # not failed -- recoverable

    # Call 2: retry now succeeds and completes.
    result = _get(handlers, ctx)
    assert result.model_dump(by_alias=True)["status"] == "completed"
    assert router.methods().count("tasks/update") == 2
    decided = [e for e in events if isinstance(e, TaskConsentDecided)]
    assert len(decided) == 1 and decided[0].granted is True


# ---------------------------------------------------------------------------
# concurrent-reprompt guard (finding #6)
# ---------------------------------------------------------------------------


def test_concurrent_reprompt_guard_short_circuits(store: GovernedTaskStore) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    order: list[str] = []
    gate = _spy_gate(order)
    # Pre-open the consent as though a first _get is already mid-flight.
    input_key = _derive_input_key(_upstream_task("T1"))
    gate.open(("S1", "T1"), input_key)
    order.clear()  # ignore the setup open

    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1", status="input_required")}})
    handlers = _handlers(store, gate, router)

    result = _get(handlers, _consent_ctx(order=order))

    # No re-prompt, no double answer relay; the still-input_required snapshot returns.
    assert "elicit" not in order
    assert "answer" not in order
    assert "tasks/update" not in router.methods()
    assert result.model_dump(by_alias=True)["status"] == "input_required"


# ---------------------------------------------------------------------------
# provenance (accept records TaskConsentDecided with the task's provenance)
# ---------------------------------------------------------------------------


def test_accept_records_consent_decided_with_provenance(store: GovernedTaskStore, events: list[object]) -> None:
    _register_corr(store, "S1", "T1", "tenant-a", "alice", "corr-42")
    router = _FakeRouter(
        {
            "tasks/get": iter(
                [
                    {"result": _upstream_task("T1", status="input_required")},
                    {"result": _upstream_task("T1", status="completed")},
                ]
            ).__next__,
            "tasks/update": {"result": {}},
        }
    )
    handlers = _handlers(store, TaskConsentGate(), router)

    _get(handlers, _consent_ctx())

    decided = [e for e in events if isinstance(e, TaskConsentDecided)]
    assert len(decided) == 1
    ev = decided[0]
    assert ev.granted is True
    assert ev.task_id == "T1"
    assert ev.target_server_id == "S1"
    assert ev.tenant_id == "tenant-a"
    assert ev.correlation_id == "corr-42"
    assert ev.principal_id == "alice"


# ---------------------------------------------------------------------------
# modern-path guard (finding #8) + no inbound tasks/update handler
# ---------------------------------------------------------------------------


def test_no_tasks_update_handler_and_modern_branch_unreachable_on_2025_session(store: GovernedTaskStore) -> None:
    handlers = _handlers(store, TaskConsentGate(), _FakeRouter())
    assert "tasks/update" not in handlers
    # The 2026-07-28 modern branch is guarded and unreachable on a 2025-11-25 session.
    assert _is_modern_tasks_session(_consent_ctx(protocol_version="2025-11-25")) is False
    assert _is_modern_tasks_session(_consent_ctx(protocol_version="2025-03-26")) is False
    assert _is_modern_tasks_session(_consent_ctx(protocol_version="2026-07-28")) is True


def test_derive_input_key_is_deterministic_and_nonempty() -> None:
    a = _derive_input_key(_upstream_task("T1", status="input_required", statusMessage="need token"))
    b = _derive_input_key(_upstream_task("T1", status="input_required", statusMessage="need token"))
    c = _derive_input_key({"inputRequests": {"z": {}, "a": {}}})
    d = _derive_input_key({"inputRequests": {"a": {}, "z": {}}})
    assert a == b and a  # deterministic + non-empty
    assert c == d  # stable ordering over the request-id set
    assert a != c
