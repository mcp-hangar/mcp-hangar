"""A relayed task's follow-ups get the answer a new call of its tool gets now (#1473).

A task is its tool's call carried on. Its follow-ups used to be checked for
ownership and a suspended session only, so a tool withdrawn, or no longer
allowed by the policy for the caller's tenant, after the task was created was
refused to a new call and still driven through ``tasks/update`` and polled for
its result. Now each follow-up asks the executor what a new call of the task's
tool would be refused with:

- ``tasks/update`` is refused, before the consent gate opens or the upstream is
  asked anything;
- ``tasks/cancel`` is never refused for this;
- ``tasks/get`` still serves a status, and refuses a poll that would hand over
  what the tool produced.

The second half checks the question itself: the executor's access and
withdrawal gates, asked for a call that is already known.

The served path, with a real reload withdrawing a tool and changing a tenant's
policy, is ``tests/integration/test_a_task_follow_up_is_checked_against_current_tool_access.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar._sdk_compat import McpError
from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.services.task_consent import TaskConsentGate
from mcp_hangar.domain.services.task_ownership import TaskOwner
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.security import PrincipalType
from mcp_hangar.fastmcp_server.task_relay_handlers import register_task_relay_handlers
from mcp_hangar.server.tools.batch import executor
from mcp_hangar.tasks_wire import EXTENSION_ID

WITHDRAWN = ("Tool 'job' is withdrawn for this tenant", "ToolWithdrawnError")
DENIED = ("Tool not available for this mcp_server", "ToolAccessDeniedError")

# ---------------------------------------------------------------------------
# The handlers
# ---------------------------------------------------------------------------


class _Router:
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[str] = []
        self.responses = responses or {}

    def __call__(self, target_server_id: str, method: str, params: dict[str, Any], timeout: float) -> Any:
        self.calls.append(method)
        return self.responses.get(method)


@contextmanager
def _as(tenant_id: str, principal_id: str) -> Iterator[None]:
    caller = CallerIdentity(
        user_id=principal_id, agent_id=None, session_id=None, principal_type="user", tenant_id=tenant_id
    )
    token = identity_context_var.set(IdentityContext(caller=caller))
    try:
        yield
    finally:
        identity_context_var.reset(token)


def _ctx(user_id: str = "alice", tenant_id: str = "tenant-a") -> Any:
    """A modern, extension-declaring caller in the SDK v2 request shape."""
    principal = SimpleNamespace(
        is_anonymous=lambda: False, id=SimpleNamespace(value=user_id), type=PrincipalType.USER, tenant_id=tenant_id
    )
    return SimpleNamespace(
        request=SimpleNamespace(
            state=SimpleNamespace(auth=SimpleNamespace(principal=principal)), headers={"mcp-name": "T1"}
        ),
        session=SimpleNamespace(
            protocol_version="2026-07-28",
            client_params=SimpleNamespace(capabilities=SimpleNamespace(extensions={EXTENSION_ID: {}})),
        ),
    )


def _task(status: str = "working", **extra: Any) -> dict[str, Any]:
    return {
        "taskId": "T1",
        "status": status,
        "createdAt": "2020-01-01T00:00:00Z",
        "lastUpdatedAt": "2020-01-01T00:00:00Z",
        "ttl": 60_000,
        **extra,
    }


def _register(store: GovernedTaskStore, *, recorded: bool = True) -> None:
    """A task on member ``S1``, created by a call of ``job`` through group ``grp``, as the relay seam records it."""
    with _as("tenant-a", "alice"):
        task = store.mint_from_upstream(_task())
        if recorded:
            store.relay_and_govern(
                target_server_id="S1",
                task=task,
                expected_owner=TaskOwner("tenant-a", "alice"),
                correlation_id="c1",
                mcp_server_id="grp",
                tool_name="job",
            )
        else:
            store.register_relayed_task(target_server_id="S1", task=task, expected_owner=TaskOwner("tenant-a", "alice"))


def _handlers(store: GovernedTaskStore, router: _Router) -> dict[str, Any]:
    handlers: dict[str, Any] = {}
    low = SimpleNamespace(add_request_handler=lambda method, _params, handler: handlers.__setitem__(method, handler))
    register_task_relay_handlers(SimpleNamespace(_mcp_server=low), store, TaskConsentGate(), router)
    return handlers


class _Asked(list[tuple[Any, ...]]):
    """What the handlers asked the executor, and what it answers them."""

    answer: tuple[str, str] | None = None


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> _Asked:
    calls = _Asked()

    def _refusal(mcp_server: str, tool: str, tenant_id: str | None, *, target_server_id: str = "") -> Any:
        calls.append((mcp_server, tool, tenant_id, target_server_id))
        return calls.answer

    monkeypatch.setattr(executor, "current_tool_access_refusal", _refusal)
    return calls


def _refuse_with(asked: _Asked, refusal: tuple[str, str] | None) -> None:
    asked.answer = refusal


def _assert_refused_as_a_call(exc: pytest.ExceptionInfo[McpError], refusal: tuple[str, str]) -> None:
    message, error_type = refusal
    assert exc.value.error.code == -32602
    assert exc.value.error.message == message
    assert exc.value.error.data == {"error_type": error_type}


@pytest.mark.parametrize("refusal", [WITHDRAWN, DENIED], ids=["withdrawn", "denied"])
async def test_an_update_is_refused_as_a_new_call_is(asked: Any, refusal: tuple[str, str]) -> None:
    store = GovernedTaskStore()
    _register(store)
    _refuse_with(asked, refusal)
    router = _Router({"tasks/get": {"result": _task("input_required")}})

    with pytest.raises(McpError) as exc:
        await _handlers(store, router)["tasks/update"](_ctx(), SimpleNamespace(task_id="T1", input_responses={"k": {}}))

    _assert_refused_as_a_call(exc, refusal)
    # Refused before the probe, so before the consent gate opens and before any answer is relayed.
    assert router.calls == []


async def test_the_check_asks_about_the_call_that_created_the_task(asked: Any) -> None:
    store = GovernedTaskStore()
    _register(store)
    router = _Router({"tasks/get": {"result": _task("input_required")}, "tasks/update": {"result": _task()}})

    await _handlers(store, router)["tasks/update"](_ctx(), SimpleNamespace(task_id="T1", input_responses={"k": {}}))

    # The group the call named, its tool, the caller's tenant and the member the task lives on.
    assert asked == [("grp", "job", "tenant-a", "S1")]
    assert router.calls == ["tasks/get", "tasks/update"]


async def test_a_cancel_is_never_refused(asked: Any) -> None:
    store = GovernedTaskStore()
    _register(store)
    _refuse_with(asked, WITHDRAWN)
    router = _Router({"tasks/cancel": {"result": _task("cancelled")}})

    await _handlers(store, router)["tasks/cancel"](_ctx(), SimpleNamespace(task_id="T1"))

    assert router.calls == ["tasks/cancel"]
    assert asked == []
    with _as("tenant-a", "alice"):
        assert store.find_owned_key("T1") is None, "a confirmed cancel retires the task"


async def test_a_status_is_still_served(asked: Any) -> None:
    store = GovernedTaskStore()
    _register(store)
    _refuse_with(asked, WITHDRAWN)
    router = _Router({"tasks/get": {"result": _task("working")}})

    polled = await _handlers(store, router)["tasks/get"](_ctx(), SimpleNamespace(task_id="T1"))

    assert polled.status == "working"
    assert polled.result is None and polled.error is None and polled.input_requests is None
    assert asked == [], "a status-only answer does not ask"


@pytest.mark.parametrize(
    "answer",
    [
        _task("completed", result={"content": [{"type": "text", "text": "out"}]}),
        _task("completed"),
        _task("failed", error={"code": -1, "message": "boom"}),
        _task("input_required", inputRequests={"k": {"method": "elicitation/create"}}),
        _task("working", result={"content": []}),
    ],
    ids=["result", "legacy-completed", "error", "input-requests", "stray-result"],
)
async def test_a_poll_carrying_what_the_tool_produced_is_refused(asked: Any, answer: dict[str, Any]) -> None:
    store = GovernedTaskStore()
    _register(store)
    _refuse_with(asked, WITHDRAWN)
    router = _Router({"tasks/get": {"result": answer}, "tasks/result": {"result": {"content": []}}})

    with pytest.raises(McpError) as exc:
        await _handlers(store, router)["tasks/get"](_ctx(), SimpleNamespace(task_id="T1"))

    _assert_refused_as_a_call(exc, WITHDRAWN)
    assert router.calls == ["tasks/get"], "a legacy upstream is never asked for the payload"


async def test_a_tool_still_allowed_is_served_as_before(asked: Any) -> None:
    store = GovernedTaskStore()
    _register(store)
    payload = {"content": [{"type": "text", "text": "out"}]}
    router = _Router({"tasks/get": {"result": _task("completed", result=payload)}})

    polled = await _handlers(store, router)["tasks/get"](_ctx(), SimpleNamespace(task_id="T1"))

    assert polled.status == "completed"
    assert polled.result == payload
    assert asked == [("grp", "job", "tenant-a", "S1")]


async def test_a_task_registered_without_its_tool_is_refused(asked: Any) -> None:
    """Fail closed: there is no tool to ask about."""
    store = GovernedTaskStore()
    _register(store, recorded=False)
    router = _Router({"tasks/get": {"result": _task("input_required")}})

    with pytest.raises(McpError) as exc:
        await _handlers(store, router)["tasks/update"](_ctx(), SimpleNamespace(task_id="T1", input_responses={"k": {}}))

    _assert_refused_as_a_call(exc, ("Tool not available for this task", "ToolAccessDeniedError"))
    assert router.calls == [] and asked == []


def test_the_store_records_the_call_that_created_a_task() -> None:
    store = GovernedTaskStore()
    _register(store)
    _register(unrecorded := GovernedTaskStore(), recorded=False)

    assert store.task_tool(("S1", "T1")) == ("grp", "job")
    assert unrecorded.task_tool(("S1", "T1")) is None
    assert store.task_tool(("S1", "nope")) is None


# ---------------------------------------------------------------------------
# The question: the access and withdrawal gates, for a call already known
# ---------------------------------------------------------------------------


class _Resolver:
    """Denies each ``(server id, group id, member server id)`` scope in ``denied``."""

    def __init__(self, denied: set[tuple[str, str | None, str | None]] = frozenset()) -> None:  # type: ignore[assignment]
        self.denied = denied
        self.asked: list[tuple[Any, ...]] = []

    def is_tool_allowed(self, *, mcp_server_id, tool_name, group_id, member_id, member_server_id) -> bool:  # noqa: ANN001
        self.asked.append((mcp_server_id, tool_name, group_id, member_id, member_server_id))
        return (mcp_server_id, group_id, member_server_id) not in self.denied


class _Projection:
    def __init__(self, withdrawn_for: set[str | None]) -> None:
        self.withdrawn_for = withdrawn_for

    def is_withdrawn_for(self, tenant_id: str | None) -> bool:
        return tenant_id in self.withdrawn_for


class _Registry:
    def __init__(self, projections: dict[str, _Projection], withdrawn_on: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.projections = projections
        self.withdrawn_on = withdrawn_on

    def resolve(self, server_id: str, tool: str, tenant_id: str | None) -> _Projection | None:
        return self.projections.get(server_id)

    def is_withdrawn(self, group_id: str, tool: str, *, tenant_id: str | None) -> bool:
        return group_id in self.withdrawn_on


def _topology(monkeypatch: pytest.MonkeyPatch, resolver: _Resolver, registry: _Registry) -> None:
    """Group ``grp`` owns member ``m1``; ``solo`` is in no group."""
    group = SimpleNamespace(members=[SimpleNamespace(id="m1")])
    monkeypatch.setattr(executor, "GROUPS", {"grp": group})
    monkeypatch.setattr(executor, "get_tool_access_resolver", lambda: resolver)
    monkeypatch.setattr(executor, "get_tool_projection_registry", lambda: registry)


def test_a_tool_every_gate_passes_is_not_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _topology(monkeypatch, resolver := _Resolver(), _Registry({}))

    assert executor.current_tool_access_refusal("solo", "job", "tenant-a", target_server_id="solo") is None
    assert resolver.asked == [("solo", "job", None, "tenant-a", None)]


def test_a_group_call_is_asked_under_the_group_and_its_member(monkeypatch: pytest.MonkeyPatch) -> None:
    _topology(monkeypatch, resolver := _Resolver({("grp", "grp", "m1")}), _Registry({}))

    assert executor.current_tool_access_refusal("grp", "job", "tenant-a", target_server_id="m1") == DENIED
    assert resolver.asked == [("grp", "job", "grp", "tenant-a", "m1")]


def test_a_member_named_directly_is_also_asked_under_its_group(monkeypatch: pytest.MonkeyPatch) -> None:
    _topology(monkeypatch, _Resolver({("grp", "grp", "m1")}), _Registry({}))

    assert executor.current_tool_access_refusal("m1", "job", "tenant-a", target_server_id="m1") == DENIED


def test_a_tool_withdrawn_for_the_tenant_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _topology(monkeypatch, _Resolver(), _Registry({"solo": _Projection({"tenant-a"})}))

    assert executor.current_tool_access_refusal("solo", "job", "tenant-a", target_server_id="solo") == WITHDRAWN
    assert executor.current_tool_access_refusal("solo", "job", "tenant-b", target_server_id="solo") is None


def test_a_group_tool_is_found_under_the_member_it_went_to(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry is keyed by the id that started, which for a group is a member (#1040)."""
    _topology(monkeypatch, _Resolver(), _Registry({"m1": _Projection({"tenant-a"})}))

    assert executor.current_tool_access_refusal("grp", "job", "tenant-a", target_server_id="m1") == WITHDRAWN


def test_a_member_named_directly_is_withdrawn_by_its_group(monkeypatch: pytest.MonkeyPatch) -> None:
    _topology(monkeypatch, _Resolver(), _Registry({}, withdrawn_on={"grp"}))

    assert executor.current_tool_access_refusal("m1", "job", "tenant-a", target_server_id="m1") == WITHDRAWN
