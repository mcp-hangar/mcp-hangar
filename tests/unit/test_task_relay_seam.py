"""ADR-014 Phase 3 -- the governed task-relay seam.

The batch executor no longer flatly rejects an upstream task handle. Under the
``relay_tasks_enabled`` kill-switch:

* a ThreadPoolExecutor WORKER only DETECTS the upstream task result and CAPTURES
  the request context into ``CallResult.relay_capture`` -- it performs NO store
  write (ADR-014 D4: governance binds on the request path, never in a worker);
* the MAIN-LOOP seam (``govern_relayed_tasks``, run by ``hangar_call`` and the
  front door's flat call) runs the
  atomic ``store.relay_and_govern`` -- register + ``TaskCreated`` emit -- BEFORE
  the handle reaches the client, binding the CAPTURED identity/pin.

With the kill-switch OFF (default) behavior is byte-identical to the ADR-008
relay-only rejection. These tests pin all of that plus the fail-closed paths.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.context import caller_polls_tasks_var, identity_context_var
from mcp_hangar.domain.events import TaskCreated
from mcp_hangar.domain.services.task_ownership import TaskOwner
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec, hangar_call
from mcp_hangar.server.tools.batch.models import CallResult, RelayCapture
from mcp_hangar.fastmcp_server.task_relay_handlers import bind_task_polling, caller_polls_tasks
from mcp_hangar.server.tools.batch.relay_seam import govern_relayed_tasks, upstream_task
from mcp_hangar.tasks_wire import EXTENSION_ID

_SERVER = "server_a"
_TOOL = "long_running_op"
_TASK_RESULT = {
    "task": {
        "taskId": "t1",
        "status": "working",
        "createdAt": "2020-01-01T00:00:00Z",
        "lastUpdatedAt": "2020-01-01T00:00:00Z",
        "ttl": 60_000,
    }
}


@pytest.fixture(autouse=True)
def _a_caller_that_can_poll() -> Iterator[None]:
    """What the task relay's middleware binds for a caller that declared the extension.

    Every test here governs a task for such a caller, but the last section's,
    which covers the caller that cannot poll one (#1405).
    """
    token = caller_polls_tasks_var.set(True)
    yield
    caller_polls_tasks_var.reset(token)


def _identity(tenant_id: str | None, principal: str | None = None) -> IdentityContext:
    return IdentityContext(
        caller=CallerIdentity(
            user_id=principal,
            agent_id=None,
            session_id=None,
            principal_type="user" if principal else "anonymous",
            tenant_id=tenant_id,
        )
    )


@contextmanager
def _bound(identity: IdentityContext | None) -> Iterator[None]:
    token = identity_context_var.set(identity)
    try:
        yield
    finally:
        identity_context_var.reset(token)


@pytest.fixture(autouse=True)
def reset_singletons() -> Iterator[None]:
    reset_tool_access_resolver()
    yield
    reset_tool_access_resolver()


def _capture(
    *,
    identity: IdentityContext | None,
    upstream: dict[str, Any] | None = None,
    target: str = _SERVER,
    call_id: str = "call-1",
) -> RelayCapture:
    return RelayCapture(
        identity=identity,
        pin=None,
        target_server_id=target,
        correlation_id=call_id,
        upstream=upstream if upstream is not None else {"task": dict(_TASK_RESULT["task"])},
        logical_mcp_server=_SERVER,
        tool=_TOOL,
    )


def _result_with_capture(capture: RelayCapture, call_id: str = "call-1") -> CallResult:
    return CallResult(
        index=0,
        call_id=call_id,
        success=True,
        result=capture.upstream,
        elapsed_ms=1.0,
        relay_capture=capture,
    )


# =============================================================================
# Worker path: DETECT + CAPTURE only -- no store write
# =============================================================================


@pytest.fixture()
def worker_ctx() -> Iterator[Mock]:
    """A batch app-ctx with the relay kill-switch ON (a spy governed_task_store)."""
    ctx = Mock()
    ctx.event_bus = Mock()
    ctx.command_bus = Mock()
    ctx.get_mcp_server.return_value = Mock(
        state=Mock(value="ready"),
        has_tools=False,
        health=Mock(should_degrade=Mock(return_value=False)),
    )
    ctx.mcp_server_exists.return_value = True
    # Kill-switch ON: store wired. It is a SPY -- if the worker ever writes to it
    # the seam-location invariant is violated.
    ctx.governed_task_store = Mock()
    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
    ):
        exec_groups.get.return_value = None
        yield ctx


def _execute_worker(ctx: Mock, upstream_result: dict) -> CallResult:
    ctx.command_bus.send.return_value = upstream_result
    with _bound(_identity("tenant-a", "alice")):
        batch = BatchExecutor().execute(
            batch_id="b",
            calls=[CallSpec(index=0, call_id="call-1", mcp_server=_SERVER, tool=_TOOL, arguments={})],
            max_concurrency=1,
            global_timeout=30.0,
            fail_fast=False,
        )
    return batch.results[0]


def test_worker_captures_and_writes_nothing_to_store(worker_ctx: Mock) -> None:
    """Kill-switch ON: the worker DETECTS the handle, CAPTURES context, and does
    NOT touch the store (all governance runs later on the main loop)."""
    result = _execute_worker(worker_ctx, _TASK_RESULT)

    # Captured, returned as a (provisional) success carrying the raw handle.
    assert result.success is True
    assert result.result == _TASK_RESULT  # full CreateTaskResult wrapper, verbatim
    assert result.relay_capture is not None
    assert result.relay_capture.upstream == _TASK_RESULT
    assert result.relay_capture.target_server_id == _SERVER
    assert result.relay_capture.tool == _TOOL
    assert result.relay_capture.correlation_id == "call-1"
    # Identity was snapshotted in the worker.
    assert result.relay_capture.identity is not None
    assert result.relay_capture.identity.caller.tenant_id == "tenant-a"

    # THE invariant: the worker performed ZERO store writes.
    worker_ctx.governed_task_store.relay_and_govern.assert_not_called()
    worker_ctx.governed_task_store.mint_from_upstream.assert_not_called()
    worker_ctx.governed_task_store.register_relayed_task.assert_not_called()


def test_worker_relay_branch_does_not_touch_group_health(worker_ctx: Mock) -> None:
    """A task creation is not a healthy-member outcome: report_success must not
    fire on the relay branch (early return before the group-health block)."""
    group_obj = Mock()
    member = Mock(
        id=Mock(value="member_a"),
        state=Mock(value="ready"),
        has_tools=False,
        health=Mock(should_degrade=Mock(return_value=False)),
    )
    group_obj.select_member_for.return_value = member
    worker_ctx.get_mcp_server.return_value = None  # force group resolution
    worker_ctx.command_bus.send.return_value = _TASK_RESULT

    with patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups:
        exec_groups.get.return_value = group_obj
        with _bound(_identity("tenant-a", "alice")):
            batch = BatchExecutor().execute(
                batch_id="b",
                calls=[CallSpec(index=0, call_id="call-1", mcp_server=_SERVER, tool=_TOOL, arguments={})],
                max_concurrency=1,
                global_timeout=30.0,
                fail_fast=False,
            )

    result = batch.results[0]
    assert result.relay_capture is not None
    # Captured against the resolved MEMBER, not the logical group.
    assert result.relay_capture.target_server_id == "member_a"
    assert result.relay_capture.logical_mcp_server == _SERVER
    group_obj.report_success.assert_not_called()
    group_obj.report_failure.assert_not_called()


# =============================================================================
# Main-loop seam: register + emit TaskCreated, byte-identical handle
# =============================================================================


def _seam_ctx(store: GovernedTaskStore) -> Mock:
    ctx = Mock()
    ctx.governed_task_store = store
    return ctx


def test_seam_governs_capture_and_emits_task_created() -> None:
    """dead-handle-killed + seam-location: the seam calls the store, emits exactly
    one TaskCreated, the governed entry exists for the owner, and the returned
    handle is byte-identical to the upstream."""
    events: list[object] = []
    store = GovernedTaskStore(event_publisher=events.append)
    identity = _identity("tenant-a", "alice")
    capture = _capture(identity=identity)
    upstream_before = dict(capture.upstream)
    executed = [_result_with_capture(capture)]

    with patch("mcp_hangar.server.tools.batch.relay_seam.get_context", return_value=_seam_ctx(store)):
        # A DIFFERENT live identity to prove the seam binds the CAPTURED one.
        with _bound(_identity("tenant-live", "mallory")):
            govern_relayed_tasks(executed)

    r = executed[0]
    assert r.success is True
    assert r.error_type is None
    # Byte-identical handle handed back to the client (the CreateTaskResult wrapper).
    assert r.result == upstream_before

    # Exactly one provenance head, attributed to the CAPTURED tenant.
    created = [e for e in events if isinstance(e, TaskCreated)]
    assert len(created) == 1
    assert created[0].task_id == "t1"
    assert created[0].tenant_id == "tenant-a"
    assert created[0].correlation_id == "call-1"

    # The governed entry exists and is owned by the captured owner.
    key = (_SERVER, "t1")
    with _bound(identity):
        assert store.get_task(key) is not None
    # Owner tenant == captured identity tenant.
    assert store._tasks[key].owner == TaskOwner("tenant-a", "alice")


def test_seam_binds_captured_identity_not_live_contextvar() -> None:
    """contextvar-liveness: the seam derives the owner from the CAPTURED snapshot,
    so a divergent live worker contextvar cannot mis-attribute the task."""
    events: list[object] = []
    store = GovernedTaskStore(event_publisher=events.append)
    capture = _capture(identity=_identity("tenant-captured", "alice"))
    executed = [_result_with_capture(capture)]

    with patch("mcp_hangar.server.tools.batch.relay_seam.get_context", return_value=_seam_ctx(store)):
        with _bound(_identity("tenant-OTHER", "bob")):  # live contextvar diverges
            govern_relayed_tasks(executed)

    assert executed[0].success is True
    key = (_SERVER, "t1")
    assert store._tasks[key].owner.tenant_id == "tenant-captured"
    # And the live identity is restored after the seam (token reset in finally).
    with _bound(_identity("tenant-OTHER", "bob")):
        assert store.get_task(key) is None  # tenant-OTHER does not own it


def test_seam_fail_closed_on_idless_upstream() -> None:
    """fail-closed extraction: an upstream task with no id -> mint raises ->
    TaskRelayRegistrationFailed, no TaskCreated, no governed entry."""
    events: list[object] = []
    store = GovernedTaskStore(event_publisher=events.append)
    capture = _capture(
        identity=_identity("tenant-a", "alice"),
        upstream={"task": {"status": "working", "createdAt": "2020-01-01T00:00:00Z"}},  # no id
    )
    executed = [_result_with_capture(capture)]

    with patch("mcp_hangar.server.tools.batch.relay_seam.get_context", return_value=_seam_ctx(store)):
        with _bound(_identity("tenant-a", "alice")):
            govern_relayed_tasks(executed)

    r = executed[0]
    assert r.success is False
    assert r.error_type == "TaskRelayRegistrationFailed"
    assert r.error_type != "TaskRelayNotSupported"  # distinct, never the misleading type
    # No event, no governed state.
    assert [e for e in events if isinstance(e, TaskCreated)] == []
    assert store._tasks == {}


def test_seam_fail_closed_when_relay_and_govern_raises() -> None:
    """Any exception from relay_and_govern -> TaskRelayRegistrationFailed and,
    thanks to the atomic rollback, zero governed state survives."""
    events: list[object] = []
    store = GovernedTaskStore(event_publisher=events.append)

    def _boom(**_kwargs: Any) -> None:
        raise RuntimeError("register blew up")

    with patch.object(store, "relay_and_govern", side_effect=_boom):
        capture = _capture(identity=_identity("tenant-a", "alice"))
        executed = [_result_with_capture(capture)]
        with patch("mcp_hangar.server.tools.batch.relay_seam.get_context", return_value=_seam_ctx(store)):
            with _bound(_identity("tenant-a", "alice")):
                govern_relayed_tasks(executed)

    r = executed[0]
    assert r.success is False
    assert r.error_type == "TaskRelayRegistrationFailed"
    assert store._tasks == {}


def test_seam_falls_back_to_rejection_when_store_absent() -> None:
    """Safety: a capture with no store to govern it (kill-switch flipped off /
    no ctx) is rewritten to the relay-only rejection, never a live handle."""
    capture = _capture(identity=_identity("tenant-a", "alice"))
    executed = [_result_with_capture(capture)]

    ctx = Mock()
    ctx.governed_task_store = None
    with patch("mcp_hangar.server.tools.batch.relay_seam.get_context", return_value=ctx):
        govern_relayed_tasks(executed)

    r = executed[0]
    assert r.success is False
    assert r.error_type == "TaskRelayNotSupported"


# =============================================================================
# Day-one regression: kill-switch OFF -> byte-identical rejection
# =============================================================================


def test_day_one_parity_kill_switch_off_is_byte_identical_rejection() -> None:
    """CRITICAL: with relay_tasks_enabled False (store absent), an upstream task
    handle yields the byte-identical TaskRelayNotSupported CallResult -- no
    capture acted on, store untouched, group-health untouched."""
    ctx = Mock()
    ctx.event_bus = Mock()
    ctx.command_bus = Mock()
    ctx.command_bus.send.return_value = _TASK_RESULT
    ctx.mcp_server_exists.return_value = True
    ctx.get_mcp_server.return_value = None  # force group path to exercise group-health guard
    ctx.governed_task_store = None  # kill-switch OFF (default)

    group_obj = Mock()
    member = Mock(
        id=Mock(value="member_a"),
        state=Mock(value="ready"),
        has_tools=False,
        health=Mock(should_degrade=Mock(return_value=False)),
    )
    group_obj.select_member_for.return_value = member

    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
    ):
        exec_groups.get.return_value = group_obj
        with _bound(_identity("tenant-a", "alice")):
            batch = BatchExecutor().execute(
                batch_id="b",
                calls=[CallSpec(index=0, call_id="call-1", mcp_server=_SERVER, tool=_TOOL, arguments={})],
                max_concurrency=1,
                global_timeout=30.0,
                fail_fast=False,
            )

    r = batch.results[0]
    # Byte-identical to the pre-change relay-only rejection.
    assert r.success is False
    assert r.error_type == "TaskRelayNotSupported"
    assert "task handle" in (r.error or "")
    assert r.result is None
    assert r.relay_capture is None  # nothing captured under the kill-switch
    assert batch.success is False
    # group-health untouched on the relay branch.
    group_obj.report_success.assert_not_called()
    group_obj.report_failure.assert_not_called()


# =============================================================================
# End-to-end: TaskCreated is emitted BEFORE hangar_call returns the response
# =============================================================================


def test_hangar_call_governs_relay_before_returning_response() -> None:
    """The relayed handle is governed (TaskCreated emitted) by the time the
    client-visible batch response is assembled -- and it carries the real handle."""
    events: list[object] = []
    store = GovernedTaskStore(event_publisher=events.append)

    ctx = Mock()
    ctx.event_bus = Mock()
    ctx.command_bus = Mock()
    ctx.command_bus.send.return_value = _TASK_RESULT
    ctx.auth_components = None  # auth off -> _authorize_calls allows
    ctx.governed_task_store = store
    provider = Mock(
        state=Mock(value="ready"),
        has_tools=False,
        health=Mock(should_degrade=Mock(return_value=False)),
    )
    ctx.get_mcp_server.side_effect = lambda k: provider if k == _SERVER else None
    ctx.mcp_server_exists.side_effect = lambda k: k == _SERVER

    with (
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.validator.GROUPS") as v_groups,
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as e_groups,
        patch("mcp_hangar.server.tools.batch.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.relay_seam.get_context", return_value=ctx),
    ):
        v_groups.get.return_value = None
        e_groups.get.return_value = None
        with _bound(_identity("tenant-a", "alice")):
            response = hangar_call(
                calls=[{"mcp_server": _SERVER, "tool": _TOOL, "arguments": {}}],
            )

    # Governed by the time the response exists.
    created = [e for e in events if isinstance(e, TaskCreated)]
    assert len(created) == 1
    assert created[0].tenant_id == "tenant-a"

    assert response["success"] is True
    assert response["succeeded"] == 1
    assert response["failed"] == 0
    call = response["results"][0]
    assert call["success"] is True
    # Byte-identical, now-governed upstream handle (the CreateTaskResult wrapper).
    assert call["result"] == _TASK_RESULT

    # The governed entry is readable by its owner.
    with _bound(_identity("tenant-a", "alice")):
        assert store.get_task((_SERVER, "t1")) is not None


# =============================================================================
# Both upstream task shapes, and who is handed a task (#1405)
# =============================================================================

#: SEP-2663's flat task result, as a spec-current upstream answers `tools/call`.
_FLAT_TASK_RESULT = {
    "resultType": "task",
    "taskId": "t1",
    "status": "working",
    "createdAt": "2020-01-01T00:00:00Z",
    "lastUpdatedAt": "2020-01-01T00:00:00Z",
    "ttlMs": 60_000,
    "pollIntervalMs": 500,
}


class TestUpstreamTask:
    def test_the_nested_shape_is_its_task(self) -> None:
        assert upstream_task({"task": {"taskId": "t1", "status": "working"}}) == {"taskId": "t1", "status": "working"}

    def test_the_flat_shape_is_its_task_under_the_ledgers_names(self) -> None:
        assert upstream_task(_FLAT_TASK_RESULT) == {
            "taskId": "t1",
            "status": "working",
            "createdAt": "2020-01-01T00:00:00Z",
            "lastUpdatedAt": "2020-01-01T00:00:00Z",
            "ttl": 60_000,
            "pollInterval": 500,
        }

    @pytest.mark.parametrize(
        "result",
        [
            None,
            [],
            "task",
            {"content": []},
            {"task": "t1"},
            {"task": {}},
            {"resultType": "complete", "taskId": "t1", "status": "working"},
            {"taskId": "t1", "status": "working"},
        ],
    )
    def test_anything_else_is_not_a_task(self, result: Any) -> None:
        assert upstream_task(result) is None


def test_seam_governs_a_flat_upstream_task_as_it_governs_a_nested_one() -> None:
    events: list[object] = []
    store = GovernedTaskStore(event_publisher=events.append)
    capture = _capture(identity=_identity("tenant-a", "alice"))
    capture.upstream = dict(_FLAT_TASK_RESULT)
    executed = [_result_with_capture(capture)]

    with patch("mcp_hangar.server.tools.batch.relay_seam.get_context", return_value=_seam_ctx(store)):
        govern_relayed_tasks(executed)

    assert executed[0].success is True
    # Handed back as the upstream sent it.
    assert executed[0].result == _FLAT_TASK_RESULT
    entry = store._tasks[(_SERVER, "t1")]
    assert entry.owner == TaskOwner("tenant-a", "alice")
    assert entry.snapshot.ttl == 60_000
    assert len([e for e in events if isinstance(e, TaskCreated)]) == 1


def test_seam_refuses_a_caller_that_cannot_poll_and_records_nothing() -> None:
    events: list[object] = []
    store = GovernedTaskStore(event_publisher=events.append)
    executed = [_result_with_capture(_capture(identity=_identity("tenant-a", "alice")))]

    token = caller_polls_tasks_var.set(False)
    try:
        with patch("mcp_hangar.server.tools.batch.relay_seam.get_context", return_value=_seam_ctx(store)):
            govern_relayed_tasks(executed)
    finally:
        caller_polls_tasks_var.reset(token)

    assert executed[0].success is False
    assert executed[0].error_type == "TasksNotNegotiated"
    assert EXTENSION_ID in (executed[0].error or "")
    assert executed[0].result is None
    assert store._tasks == {}
    assert events == []


def test_a_request_nothing_bound_is_one_whose_caller_cannot_poll() -> None:
    """A path the task relay's middleware never wrapped is handed no task."""
    store = GovernedTaskStore()
    executed = [_result_with_capture(_capture(identity=_identity("tenant-a", "alice")))]

    with patch("mcp_hangar.server.tools.batch.relay_seam.get_context", return_value=_seam_ctx(store)):
        contextvars.Context().run(govern_relayed_tasks, executed)

    assert executed[0].error_type == "TasksNotNegotiated"
    assert store._tasks == {}


def _request(version: str | None, declared: bool) -> SimpleNamespace:
    """A `ServerRequestContext`, as far as who-can-poll reads it."""
    extensions: dict[str, Any] = {EXTENSION_ID: {}} if declared else {}
    capabilities = SimpleNamespace(extensions=extensions)
    session = SimpleNamespace(protocol_version=version, client_params=SimpleNamespace(capabilities=capabilities))
    return SimpleNamespace(session=session)


class TestWhoCanPollATask:
    @pytest.mark.parametrize(
        ("version", "declared", "polls"),
        [
            ("2026-07-28", True, True),
            ("2026-07-28", False, False),
            ("2025-11-25", True, False),
            ("2025-06-18", True, False),
            (None, True, False),
        ],
    )
    def test_a_caller_on_a_revision_with_tasks_that_declared_the_extension(
        self, version: str | None, declared: bool, polls: bool
    ) -> None:
        assert caller_polls_tasks(_request(version, declared)) is polls

    def test_a_request_with_no_session_cannot(self) -> None:
        assert caller_polls_tasks(SimpleNamespace()) is False

    def test_the_middleware_binds_it_for_the_request_and_releases_it(self) -> None:
        seen: list[bool] = []

        async def call_next(ctx: Any) -> str:
            seen.append(caller_polls_tasks_var.get())
            return "served"

        async def request() -> tuple[str, bool]:
            caller_polls_tasks_var.set(False)
            served = await bind_task_polling(_request("2026-07-28", True), call_next)
            return served, caller_polls_tasks_var.get()

        assert asyncio.run(request()) == ("served", False)
        assert seen == [True]
