"""Unit tests for the SEP-2663 relayed-task serving surface (ADR-014 / ADR-015).

Exercises the three ``tasks/*`` handlers registered by
:func:`register_task_relay_handlers` against a REAL :class:`GovernedTaskStore`, a
fake injected upstream router, and a fake request context carrying an
authenticated principal, a negotiated protocol version, a client capability
declaration and the ``Mcp-Name`` routing header. Handlers are invoked directly.

Invariants under test: the SEP-2663 wire shapes (flat, ``ttlMs``, outcome
inlined), the refusal ladder (``-32601`` legacy / ``-32020`` routing header /
``-32021`` undeclared extension / ``-32602`` unknown task), upstream-truthful
snapshot sync, once-only ``TaskCompleted``/``TaskCancelled`` emission, empty
cancel/update acknowledgements, cross-tenant denial with no existence leak, and
digest re-verification before an outcome is handed over.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar._sdk_compat import McpError
from mcp_hangar.tasks_wire import (
    EXTENSION_ID,
    HEADER_MISMATCH,
    MISSING_REQUIRED_CLIENT_CAPABILITY,
    EmptyResult,
    GetTaskResult,
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


def _request_with(principal: Any, headers: dict[str, str] | None) -> Any:
    return SimpleNamespace(
        state=SimpleNamespace(auth=SimpleNamespace(principal=principal)),
        headers=headers,
    )


def _session(*, version: str | None, declares: bool) -> Any:
    """A fake session carrying the negotiated version and client capabilities."""
    extensions = {EXTENSION_ID: {}} if declares else {}
    return SimpleNamespace(
        protocol_version=version,
        client_params=SimpleNamespace(capabilities=SimpleNamespace(extensions=extensions)),
    )


def _ctx(
    user_id: str | None = None,
    tenant_id: str | None = None,
    *,
    task_id: str | None = "T1",
    version: str | None = "2026-07-28",
    declares: bool = True,
) -> Any:
    """A fake ctx in the SDK **v2** shape: the request hangs off ``ctx.request``.

    This is what ``ServerRequestContext`` actually looks like on ``mcp>=2``; there
    is no ``request_context`` attribute. Pinning the v1 shape here is what let the
    bridge silently no-op under auth in production while these tests stayed green.

    Defaults to a caller the SEP-2663 ladder accepts -- modern version, extension
    declared, ``Mcp-Name`` matching ``task_id`` -- so that a test about anything
    else is not silently testing the gate. Each knob is turned off individually
    by the refusal-ladder tests.
    """
    principal = _principal(user_id, tenant_id) if user_id else None
    headers = {"mcp-name": task_id} if task_id is not None else {}
    return SimpleNamespace(
        request=_request_with(principal, headers),
        session=_session(version=version, declares=declares),
    )


def _ctx_v1(user_id: str | None = None, tenant_id: str | None = None, *, task_id: str = "T1") -> Any:
    """The SDK **v1** shape: ``ctx.request_context.request``. Still supported."""
    principal = _principal(user_id, tenant_id) if user_id else None
    return SimpleNamespace(
        request_context=SimpleNamespace(request=_request_with(principal, {"mcp-name": task_id})),
        session=_session(version="2026-07-28", declares=True),
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


def test_nested_task_form_is_locked_out() -> None:
    """The anti-pattern ``GetTaskResult(task=...)`` must raise (results are FLAT)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GetTaskResult(task={"taskId": "T1", "status": "working"})  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Registration surface
# ---------------------------------------------------------------------------


def test_registers_exactly_the_sep_2663_method_set(store: GovernedTaskStore) -> None:
    """Three methods, and the absence of the other two is the point.

    SEP-2663 removes `tasks/result` and `tasks/list`. An unregistered method
    already yields -32601 from the SDK runner, so not registering them IS the
    implementation of "removed" -- there is no separate rejection to write.
    """
    handlers = _handlers(store, _FakeRouter())

    assert set(handlers) == {"tasks/get", "tasks/cancel", "tasks/update"}


def test_update_is_registered_unconditionally(store: GovernedTaskStore) -> None:
    """It used to be gated on the SDK defining ``UpdateTaskRequest``.

    That probe watched `mcp_types`, which carries the frozen SEP-1686
    generation, so it could never become true and the handler was dead code on
    every SDK version (ADR-015).
    """
    handlers = _handlers(store, _FakeRouter())

    assert "tasks/update" in handlers


def test_param_models_match_the_vendored_wire_definitions(store: GovernedTaskStore) -> None:
    """The registered params subclass the SDK base but must not drift from the wire.

    They deliberately subclass `RequestParams` rather than the `tasks_wire`
    models so `_meta` is parsed and forwarded; this pins that the two field sets
    stay identical anyway.
    """
    from mcp_hangar import tasks_wire

    handlers = _handlers(store, _FakeRouter())

    def fields(model: Any) -> set[str]:
        return set(model.model_fields) - {"meta"}

    assert fields(handlers["tasks/get"][0]) == fields(tasks_wire.GetTaskRequestParams)
    assert fields(handlers["tasks/cancel"][0]) == fields(tasks_wire.CancelTaskRequestParams)
    assert fields(handlers["tasks/update"][0]) == fields(tasks_wire.UpdateTaskRequestParams)


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
        asyncio.run(
            handlers["tasks/get"][1](_ctx("alice", "tenant-a", task_id="NOPE"), SimpleNamespace(task_id="NOPE"))
        )
    assert "Task not found: NOPE" in str(exc.value)


# ---------------------------------------------------------------------------
# tasks/cancel truthfulness
# ---------------------------------------------------------------------------


def test_cancel_confirmed_retires_the_entry_and_emits_once(store: GovernedTaskStore, events: list[object]) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/cancel": {"result": _upstream_task("T1", status="cancelled")}})
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/cancel"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    assert isinstance(result, EmptyResult)
    cancelled = [e for e in events if isinstance(e, TaskCancelled)]
    assert len(cancelled) == 1
    assert cancelled[0].task_id == "T1"
    # Entry retired: a follow-up cancel now denies (not found), no double event.
    with pytest.raises(McpError):
        asyncio.run(handlers["tasks/cancel"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))
    assert len([e for e in events if isinstance(e, TaskCancelled)]) == 1


def test_the_cancel_ack_carries_no_status_at_all(store: GovernedTaskStore) -> None:
    """SEP-2663: cancellation is cooperative, so the ack must not claim an outcome.

    The SEP-1686 shape this replaced returned a status -- and on the confirmed
    path OVERWROTE it with ``cancelled`` before returning, which is exactly the
    fabrication the SEP warns about. The client polls ``tasks/get`` instead.
    """
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/cancel": {"result": _upstream_task("T1", status="cancelled")}})
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/cancel"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))
    wire = result.model_dump(by_alias=True, exclude_none=True)

    assert wire == {"resultType": "complete"}


def test_cancel_upstream_error_keeps_the_entry_and_emits_nothing(
    store: GovernedTaskStore, events: list[object]
) -> None:
    """An unconfirmed cancel must not retire the ledger entry or claim success."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter(
        {
            "tasks/cancel": {"error": {"code": -32000, "message": "refused"}},
            "tasks/get": {"result": _upstream_task("T1", status="working")},
        }
    )
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/cancel"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    assert isinstance(result, EmptyResult)
    assert [e for e in events if isinstance(e, TaskCancelled)] == []
    # Entry KEPT: the task is still pollable and still reports its TRUE status.
    polled = asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))
    assert polled.status == "working"


def test_cancel_upstream_reports_non_cancelled_status_keeps_entry(
    store: GovernedTaskStore, events: list[object]
) -> None:
    """The upstream answered cleanly but is still working -- not a confirmation."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter(
        {
            "tasks/cancel": {"result": _upstream_task("T1", status="working")},
            "tasks/get": {"result": _upstream_task("T1", status="working")},
        }
    )
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/cancel"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    assert isinstance(result, EmptyResult)
    assert [e for e in events if isinstance(e, TaskCancelled)] == []
    polled = asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))
    assert polled.status == "working"


def test_cancel_confirmed_predicate() -> None:
    assert _cancel_confirmed({"result": {"status": "cancelled"}}) is True
    assert _cancel_confirmed({"result": {}}) is True  # clean result, no contradicting status
    assert _cancel_confirmed({"result": None}) is False
    assert _cancel_confirmed({"error": {"code": -1}}) is False
    assert _cancel_confirmed({"result": {"status": "working"}}) is False
    assert _cancel_confirmed("nonsense") is False


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


def test_owner_reaches_their_own_task_via_the_v2_request_shape(store: GovernedTaskStore) -> None:
    """The owner must reach their own task when the ctx is SDK-v2 shaped.

    Regression: the bridge read only ``ctx.request_context.request`` (v1). On v2
    that attribute does not exist, so every ``tasks/*`` call on the shipped
    ``serve --http`` path was unattributed -- the serve path binds the principal
    on ``request.state.auth`` and not on ``identity_context_var`` -- and an owner
    got "Task not found" for their own task. Fail-closed, but the relay was dead
    under auth.
    """
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1", status="completed")}})
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    assert result.task_id == "T1"
    assert router.calls, "the relay never reached the upstream"


def test_owner_reaches_their_own_task_via_the_v1_request_shape(store: GovernedTaskStore) -> None:
    """The v1 ``ctx.request_context.request`` spelling keeps working."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1", status="completed")}})
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/get"][1](_ctx_v1("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    assert result.task_id == "T1"


def test_a_foreign_tenant_still_cannot_reach_the_task_through_the_v2_shape(store: GovernedTaskStore) -> None:
    """Bridging identity must not widen access: another tenant is still denied."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1")}})
    handlers = _handlers(store, router)

    with pytest.raises(McpError):
        asyncio.run(handlers["tasks/get"][1](_ctx("bob", "tenant-b"), SimpleNamespace(task_id="T1")))
    assert router.calls == []


# ---------------------------------------------------------------------------
# The SEP-2663 refusal ladder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["tasks/get", "tasks/cancel", "tasks/update"])
def test_legacy_connection_is_told_the_method_does_not_exist(store: GovernedTaskStore, method: str) -> None:
    """A 2025-11-25 client gets -32601, not -32021.

    It could not act on -32021 if it wanted to: the extension it would be told
    to declare cannot be negotiated on its protocol generation. For it these
    methods genuinely do not exist.
    """
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter()
    handlers = _handlers(store, router)
    params = SimpleNamespace(task_id="T1", input_responses={"k": {}})

    with pytest.raises(McpError) as exc:
        asyncio.run(handlers[method][1](_ctx("alice", "tenant-a", version="2025-11-25"), params))

    assert exc.value.error.code == -32601
    assert router.calls == [], "a refused request must never reach the upstream"


def test_an_unreadable_protocol_version_fails_closed_to_legacy(store: GovernedTaskStore) -> None:
    """No negotiated version -> treated as legacy, not as modern."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    handlers = _handlers(store, _FakeRouter())

    with pytest.raises(McpError) as exc:
        asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a", version=None), SimpleNamespace(task_id="T1")))

    assert exc.value.error.code == -32601


@pytest.mark.parametrize("method", ["tasks/get", "tasks/cancel", "tasks/update"])
def test_modern_client_without_the_extension_is_told_what_to_declare(store: GovernedTaskStore, method: str) -> None:
    """-32021 carries a machine-readable `requiredCapabilities`.

    Unlike a legacy client, this one can fix its declaration and retry, so it is
    told exactly what to add rather than simply refused.
    """
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter()
    handlers = _handlers(store, router)
    params = SimpleNamespace(task_id="T1", input_responses={"k": {}})

    with pytest.raises(McpError) as exc:
        asyncio.run(handlers[method][1](_ctx("alice", "tenant-a", declares=False), params))

    assert exc.value.error.code == MISSING_REQUIRED_CLIENT_CAPABILITY == -32021
    assert exc.value.error.data == {"requiredCapabilities": {"extensions": {EXTENSION_ID: {}}}}
    assert router.calls == []


@pytest.mark.parametrize("method", ["tasks/get", "tasks/cancel", "tasks/update"])
def test_a_missing_mcp_name_header_is_refused(store: GovernedTaskStore, method: str) -> None:
    """SEP-2663 mandates `Mcp-Name: <taskId>` on every `tasks/*` over HTTP.

    The header only earns its keep if it is reliably present -- an intermediary
    routing on it cannot fall back to parsing the body. Neither the SDK nor the
    front-door middleware enforces this (the SDK's `NAME_BEARING_METHODS` omits
    `tasks/*`; the middleware disengages on 2026-07-28), so this gate is the
    only rung that runs.
    """
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter()
    handlers = _handlers(store, router)
    params = SimpleNamespace(task_id="T1", input_responses={"k": {}})

    with pytest.raises(McpError) as exc:
        asyncio.run(handlers[method][1](_ctx("alice", "tenant-a", task_id=None), params))

    assert exc.value.error.code == HEADER_MISMATCH == -32020
    assert router.calls == []


def test_an_mcp_name_header_disagreeing_with_the_body_is_refused(store: GovernedTaskStore) -> None:
    """Worse than absent: an intermediary already routed on a value the body denies."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter()
    handlers = _handlers(store, router)

    with pytest.raises(McpError) as exc:
        asyncio.run(
            handlers["tasks/get"][1](_ctx("alice", "tenant-a", task_id="SOMEONE-ELSE"), SimpleNamespace(task_id="T1"))
        )

    assert exc.value.error.code == HEADER_MISMATCH
    assert router.calls == []


def test_the_ladder_is_ordered_header_before_capability(store: GovernedTaskStore) -> None:
    """A misrouted request is refused as misrouted whatever the client declared.

    Both defects are present here. The answer must be the routing one: it is a
    property of the request, not of the client's declaration, so it cannot be
    allowed to depend on how far down the ladder the request happens to get.
    """
    _register(store, "S1", "T1", "tenant-a", "alice")
    handlers = _handlers(store, _FakeRouter())

    with pytest.raises(McpError) as exc:
        asyncio.run(
            handlers["tasks/get"][1](
                _ctx("alice", "tenant-a", task_id=None, declares=False), SimpleNamespace(task_id="T1")
            )
        )

    assert exc.value.error.code == HEADER_MISMATCH


def test_the_ladder_is_ordered_version_before_header(store: GovernedTaskStore) -> None:
    """Never demand a routing header for a method that does not exist for you."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    handlers = _handlers(store, _FakeRouter())

    with pytest.raises(McpError) as exc:
        asyncio.run(
            handlers["tasks/get"][1](
                _ctx("alice", "tenant-a", version="2025-11-25", task_id=None), SimpleNamespace(task_id="T1")
            )
        )

    assert exc.value.error.code == -32601


def test_stdio_has_no_headers_so_the_requirement_does_not_apply(store: GovernedTaskStore) -> None:
    """SEP-2663 scopes `Mcp-Name` to Streamable HTTP.

    On stdio there is no request object to carry it, so its absence means "not
    applicable" rather than "missing" -- refusing there would make the relay
    unusable on a transport the SEP never addressed.
    """
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1")}})
    handlers = _handlers(store, router)
    ctx = SimpleNamespace(request=None, session=_session(version="2026-07-28", declares=True))

    with _as("tenant-a", "alice"):
        result = asyncio.run(handlers["tasks/get"][1](ctx, SimpleNamespace(task_id="T1")))

    assert result.task_id == "T1"


# ---------------------------------------------------------------------------
# SEP-2663 wire shape on tasks/get
# ---------------------------------------------------------------------------


def test_get_projects_the_ledger_snapshot_onto_the_sep_2663_field_names(store: GovernedTaskStore) -> None:
    """The ledger still stores the SEP-1686 `Task`; the wire must not show it.

    `ttl` -> `ttlMs` and `poll_interval` -> `pollIntervalMs` are pure renames --
    the fossil documents both as milliseconds -- but the fossil spellings must
    never reach a client.
    """
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1")}})
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))
    wire = result.model_dump(by_alias=True)

    assert wire["ttlMs"] == 60_000
    assert "ttl" not in wire
    assert "pollInterval" not in wire
    assert wire["resultType"] == "complete"
    assert "task" not in wire, "results are flat; the nested fossil form must not reappear"


def test_a_completed_task_inlines_its_result_on_the_poll(store: GovernedTaskStore) -> None:
    """SEP-2663 folds the removed `tasks/result` round trip into `tasks/get`."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    payload = {"content": [{"type": "text", "text": "done"}], "isError": False}
    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1", status="completed", result=payload)}})
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    assert result.status == "completed"
    assert result.result == payload


def test_input_requests_reach_the_client_so_it_can_answer_them(store: GovernedTaskStore) -> None:
    """Without this map the client cannot key its `tasks/update` answers.

    python-sdk#3005's own `GetTaskResult` drops it (no field + `extra="ignore"`),
    which is the one place the vendored models deliberately diverge.
    """
    _register(store, "S1", "T1", "tenant-a", "alice")
    requests = {"user_name": {"message": "Who are you?"}}
    router = _FakeRouter(
        {"tasks/get": {"result": _upstream_task("T1", status="input_required", inputRequests=requests)}}
    )
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    assert result.input_requests == requests
    assert result.model_dump(by_alias=True)["inputRequests"] == requests


def test_an_upstream_error_yields_no_outcome_fields(store: GovernedTaskStore) -> None:
    """State is never fabricated: no upstream answer means no inlined outcome."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/get": {"error": {"code": -32000, "message": "boom"}}})
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    assert result.result is None
    assert result.error is None
    assert result.status == "working"


def test_digest_drift_is_caught_before_an_outcome_is_handed_over(
    store: GovernedTaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check that used to guard `tasks/result` must survive its removal.

    With that method gone, `tasks/get` is the ONLY path by which a task's
    payload reaches a caller. Dropping the re-verification along with the method
    would have quietly retired a supply-chain control (ADR-014) while looking
    like a pure wire change.
    """
    from mcp_hangar._sdk_compat import INVALID_PARAMS, make_mcp_error

    _register(store, "S1", "T1", "tenant-a", "alice")
    payload = {"content": [], "isError": False}
    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1", status="completed", result=payload)}})

    def _drift(key: Any) -> None:
        raise make_mcp_error(INVALID_PARAMS, "tool digest drifted since task creation")

    monkeypatch.setattr(store, "_verify_pinned_digest", _drift)
    handlers = _handlers(store, router)

    with pytest.raises(McpError) as exc:
        asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    assert "digest drifted" in str(exc.value)


def test_a_still_running_task_is_not_digest_checked(store: GovernedTaskStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """The check guards handing over an outcome, matching its old placement.

    A poll that returns no payload has nothing to verify, and failing one would
    kill live tasks on an unrelated deployment.
    """
    from mcp_hangar._sdk_compat import INVALID_PARAMS, make_mcp_error

    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1", status="working")}})

    def _drift(key: Any) -> None:
        raise make_mcp_error(INVALID_PARAMS, "tool digest drifted since task creation")

    monkeypatch.setattr(store, "_verify_pinned_digest", _drift)
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    assert result.status == "working"


# ---------------------------------------------------------------------------
# tasks/update
# ---------------------------------------------------------------------------


def test_update_relays_the_clients_answers_and_acknowledges_empty(store: GovernedTaskStore) -> None:
    _register(store, "S1", "T1", "tenant-a", "alice")
    answers = {"user_name": {"content": {"name": "ada"}}}
    router = _FakeRouter(
        {
            "tasks/get": {"result": _upstream_task("T1", status="input_required")},
            "tasks/update": {"result": _upstream_task("T1", status="working")},
        }
    )
    handlers = _handlers(store, router)

    result = asyncio.run(
        handlers["tasks/update"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1", input_responses=answers))
    )

    assert isinstance(result, EmptyResult)
    relayed = [c for c in router.calls if c[1] == "tasks/update"]
    assert len(relayed) == 1


def test_update_from_a_foreign_tenant_is_denied_before_the_gate_or_upstream(
    store: GovernedTaskStore,
) -> None:
    """Tenant authorization sits ABOVE consent, structurally.

    Re-homed from the deleted synchronous-consent suite, where it guarded the
    elicit path. The property is the same and still load-bearing: a foreign
    tenant must be refused before anything is opened or relayed, and must not be
    able to tell "not yours" from "does not exist".
    """
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter()
    gate = TaskConsentGate()
    handlers = _handlers(store, router, gate)

    with pytest.raises(McpError) as exc:
        asyncio.run(
            handlers["tasks/update"][1](
                _ctx("bob", "tenant-b"), SimpleNamespace(task_id="T1", input_responses={"k": {}})
            )
        )

    assert "Task not found: T1" in str(exc.value)
    assert router.calls == [], "a denied caller must never reach the upstream"


# ---------------------------------------------------------------------------
# Bridging an upstream that keeps the payload behind tasks/result
# ---------------------------------------------------------------------------


def test_a_completed_task_on_an_older_upstream_still_yields_its_payload(store: GovernedTaskStore) -> None:
    """The regression this guards against made every such payload unreachable.

    SEP-2663 inlines a completed task's result on `tasks/get`, so a modern
    upstream needs nothing extra. An upstream on the older design answers
    `tasks/get` with a status only and keeps the payload behind `tasks/result`.
    Hangar stopped serving that method downstream -- correctly -- but for a while
    also stopped CALLING it upstream, so a client polled to `completed` and got
    `result: null` forever. Bridging the generations is the relay's job.
    """
    _register(store, "S1", "T1", "tenant-a", "alice")
    payload = {"content": [{"type": "text", "text": "done"}], "isError": False}
    router = _FakeRouter(
        {
            # No inlined `result` -- the older shape.
            "tasks/get": {"result": _upstream_task("T1", status="completed")},
            "tasks/result": {"result": payload},
        }
    )
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    assert result.result == payload
    assert any(call[1] == "tasks/result" for call in router.calls)


def test_a_modern_upstream_is_never_asked_for_the_payload_twice(store: GovernedTaskStore) -> None:
    """An inlined result is authoritative; asking again would be a wasted round trip."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    payload = {"content": [], "isError": False}
    router = _FakeRouter({"tasks/get": {"result": _upstream_task("T1", status="completed", result=payload)}})
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    assert result.result == payload
    assert not any(call[1] == "tasks/result" for call in router.calls)


def test_an_upstream_that_refuses_tasks_result_does_not_fail_the_poll(store: GovernedTaskStore) -> None:
    """A modern upstream answers -32601 there. That is not a reason to fail a good poll."""
    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter(
        {
            "tasks/get": {"result": _upstream_task("T1", status="completed")},
            "tasks/result": {"error": {"code": -32601, "message": "Method not found"}},
        }
    )
    handlers = _handlers(store, router)

    result = asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    assert result.status == "completed"
    assert result.result is None


def test_a_drifted_digest_is_caught_before_the_payload_is_even_requested(
    store: GovernedTaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordering matters: never ask a tool that failed verification for output."""
    from mcp_hangar._sdk_compat import INVALID_PARAMS, make_mcp_error

    _register(store, "S1", "T1", "tenant-a", "alice")
    router = _FakeRouter(
        {
            "tasks/get": {"result": _upstream_task("T1", status="completed")},
            "tasks/result": {"result": {"content": []}},
        }
    )

    def _drift(key: Any) -> None:
        raise make_mcp_error(INVALID_PARAMS, "tool digest drifted since task creation")

    monkeypatch.setattr(store, "_verify_pinned_digest", _drift)
    handlers = _handlers(store, router)

    with pytest.raises(McpError):
        asyncio.run(handlers["tasks/get"][1](_ctx("alice", "tenant-a"), SimpleNamespace(task_id="T1")))

    assert not any(call[1] == "tasks/result" for call in router.calls)
