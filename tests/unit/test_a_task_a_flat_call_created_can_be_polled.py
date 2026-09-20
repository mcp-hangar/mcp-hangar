"""A task a front door's flat `tools/call` created is its owner's to poll, and only its owner's (#1394).

`hangar_call` records who owns a task an upstream creates before the handle
reaches its caller, and `tasks/get`, `tasks/cancel` and `tasks/update` answer
only that owner. The flat call path dispatched through the same executor and
skipped the recording. On a front door, every `tasks/*` on such a task answered
"Task not found", its creator included. With the SDK Hangar ships, the call failed
before that, with `-32602`: the upstream's nested task handle is not a tool result.

What is driven is the app `mcp-hangar serve --http` serves, composed by the
security-predicate reachability test's harness, and its upstream's `long_job`,
which answers with a task handle. Each topology creates the task its own way:
`hangar_call` on egress, the flat call on the front door. Then three callers send
each `tasks/*` method: the owner, a peer in the owner's tenant, and a caller in
another tenant. Both topologies must give each of them the same answer. The
`tasks/*` handlers do not know which path created a task, and must not need to.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

import pytest

from mcp_hangar._sdk_compat import INVALID_PARAMS
from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.context import caller_polls_tasks_var
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.fastmcp_server.flat_call_tasks import govern_flat_call
from mcp_hangar.server.tools.batch.models import CallResult, RelayCapture
from mcp_hangar.tasks_wire import CreateTaskResult
from tests.unit.test_security_predicates_are_reachable import (
    _MINT_TASK,
    _TASK,
    _TASK_ID,
    _TASKS,
    _Gateway,
    _Probe,
    _served,
    _served_ok,
    _upstream,
)

#: The harness's one API key: `svc:caller`, in `tenant-a`.
_OWNER = "the owner"
#: Everyone else, by name: (principal, tenant).
_NON_OWNERS = {
    "a peer in the owner's tenant": ("svc:peer", "tenant-a"),
    "a caller in another tenant": ("svc:other", "tenant-b"),
}
#: The owner goes last: its `tasks/cancel` and `tasks/update` act on the task.
_CALLERS = (*_NON_OWNERS, _OWNER)
_METHODS = ("tasks/get", "tasks/cancel", "tasks/update")
_CREATE = "create"
_NOT_FOUND = {"code": INVALID_PARAMS, "message": f"Task not found: {_TASK_ID}"}

_Answer = tuple[int, dict[str, Any]]


def _task_request(topology: str, method: str) -> _Probe:
    params: dict[str, Any] = {"taskId": _TASK_ID}
    if method == "tasks/update":
        params["inputResponses"] = {}
    return _Probe(topology, method, params, name=_TASK_ID, capabilities=_TASKS)


def _exchange(topology: str, owner: _Gateway) -> dict[tuple[str, str], _Answer]:
    """Create the task as its owner, then send every method as every caller."""
    from mcp_hangar.server.context import get_context

    keys = get_context().auth_components.api_key_store
    gateways = {_OWNER: owner}
    for caller, (principal, tenant) in _NON_OWNERS.items():
        key = keys.create_key(principal_id=principal, name=principal, tenant_id=tenant)
        gateways[caller] = _Gateway(client=owner.client, api_key=key)

    answers = {(_CREATE, _OWNER): owner.send(_MINT_TASK[topology])}
    for caller in _CALLERS:
        for method in _METHODS:
            answers[(method, caller)] = gateways[caller].send(_task_request(topology, method))
    return answers


@pytest.fixture(scope="module")
def answers(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[tuple[str, str], _Answer]]:
    """Every caller's answer to every method, on each topology."""
    from mcp_hangar.fastmcp_server import asgi

    out: dict[str, dict[tuple[str, str], _Answer]] = {}
    with _upstream() as upstream_url, pytest.MonkeyPatch.context() as env:
        # Loopback is a default trusted proxy, so the `x-session-id` each request
        # carries is honoured whatever an earlier test left behind.
        env.delenv("MCP_TRUSTED_PROXIES", raising=False)
        asgi._forwarded_session_extractor.cache_clear()
        try:
            for topology in ("egress", "front_door"):
                with _served(topology, upstream_url, tmp_path_factory.mktemp(topology)) as owner:
                    out[topology] = _exchange(topology, owner)
        finally:
            asgi._forwarded_session_extractor.cache_clear()
    return out


class TestTheFlatCallAnswersWithTheTask:
    def test_it_is_a_task_result(self, answers: dict[str, dict[tuple[str, str], _Answer]]) -> None:
        status, payload = answers["front_door"][(_CREATE, _OWNER)]
        result = payload.get("result") or {}

        assert status == 200, payload
        assert result.get("resultType") == "task", payload
        assert result.get("taskId") == _TASK_ID
        assert result.get("status") == "working"
        assert result.get("ttlMs") == _TASK["ttl"]

    def test_its_fields_are_the_ones_tasks_get_serves(self, answers: dict[str, dict[tuple[str, str], _Answer]]) -> None:
        created = answers["front_door"][(_CREATE, _OWNER)][1]["result"]
        polled = answers["front_door"][("tasks/get", _OWNER)][1]["result"]
        fields = ("taskId", "status", "createdAt", "lastUpdatedAt", "ttlMs")

        assert {f: created.get(f) for f in fields} == {f: polled.get(f) for f in fields}


class TestTheOwnerIsServed:
    @pytest.mark.parametrize("method", _METHODS)
    def test_on_the_front_door(self, answers: dict[str, dict[tuple[str, str], _Answer]], method: str) -> None:
        status, payload = answers["front_door"][(method, _OWNER)]

        assert _served_ok(status, payload), f"{method} from the task's owner was not served: {status} {payload}"


class TestEveryoneElseIsRefused:
    @pytest.mark.parametrize("caller", list(_NON_OWNERS))
    @pytest.mark.parametrize("method", _METHODS)
    def test_on_the_front_door(
        self, answers: dict[str, dict[tuple[str, str], _Answer]], method: str, caller: str
    ) -> None:
        status, payload = answers["front_door"][(method, caller)]

        assert payload.get("error") == _NOT_FOUND, f"{method} from {caller}: {status} {payload}"


class TestBothPathsAnswerAlike:
    """The same ownership rules, whichever path created the task.

    Compared whole, status and payload, so a refusal that differed in shape
    between the paths would fail here.
    """

    @pytest.mark.parametrize("caller", _CALLERS)
    @pytest.mark.parametrize("method", _METHODS)
    def test_the_front_door_answers_as_hangar_call_does(
        self, answers: dict[str, dict[tuple[str, str], _Answer]], method: str, caller: str
    ) -> None:
        assert answers["front_door"][(method, caller)] == answers["egress"][(method, caller)]


# --- the flat path's half of the seam, on hand-built results ------------------------------
#
# The served tests above are the proof. These pin the outcomes the served app
# cannot be made to reach without patching it: a task the seam cannot govern, and
# a result that is not a task.


def _captured_task(upstream: dict[str, Any] | None = None) -> list[CallResult]:
    capture = RelayCapture(
        identity=IdentityContext(
            caller=CallerIdentity(
                user_id="alice", agent_id=None, session_id=None, principal_type="user", tenant_id="tenant-a"
            )
        ),
        pin=None,
        target_server_id="upstream",
        correlation_id="call-1",
        upstream=upstream if upstream is not None else {"task": dict(_TASK)},
        logical_mcp_server="upstream",
        tool="long_job",
    )
    return [CallResult(index=0, call_id="call-1", success=True, result=capture.upstream, relay_capture=capture)]


def _with_store(store: Any) -> Any:
    return patch("mcp_hangar.server.tools.batch.relay_seam.get_context", return_value=Mock(governed_task_store=store))


class TestGovernFlatCall:
    @pytest.fixture(autouse=True)
    def _a_caller_that_can_poll(self) -> Any:
        """What the task relay's middleware binds for a caller that declared the extension."""
        token = caller_polls_tasks_var.set(True)
        yield
        caller_polls_tasks_var.reset(token)

    def test_a_governed_task_is_answered_with_its_task_result(self) -> None:
        store = GovernedTaskStore()

        with _with_store(store):
            result, created = govern_flat_call(_captured_task())

        assert result.success
        assert isinstance(created, CreateTaskResult)
        assert created.task_id == _TASK_ID
        owner = store._tasks[("upstream", _TASK_ID)].owner
        assert (owner.tenant_id, owner.principal_id) == ("tenant-a", "alice")

    def test_an_upstream_task_in_the_flat_shape_is_governed_and_answered_alike(self) -> None:
        """SEP-2663's flat `resultType: "task"`, with its `ttlMs`, is the same task (#1405)."""
        flat = {"resultType": "task", **{k: v for k, v in _TASK.items() if k != "ttl"}, "ttlMs": _TASK["ttl"]}
        store = GovernedTaskStore()

        with _with_store(store):
            result, created = govern_flat_call(_captured_task(flat))

        assert result.success
        assert isinstance(created, CreateTaskResult)
        assert (created.task_id, created.ttl_ms) == (_TASK_ID, _TASK["ttl"])
        assert store._tasks[("upstream", _TASK_ID)].owner.principal_id == "alice"

    def test_a_caller_that_cannot_poll_is_refused_and_nothing_is_recorded(self) -> None:
        store = GovernedTaskStore()
        token = caller_polls_tasks_var.set(False)
        try:
            with _with_store(store):
                result, created = govern_flat_call(_captured_task())
        finally:
            caller_polls_tasks_var.reset(token)

        assert not result.success
        assert result.error_type == "TasksNotNegotiated"
        assert "io.modelcontextprotocol/tasks" in (result.error or "")
        assert created is None
        assert store._tasks == {}

    def test_a_task_the_store_cannot_record_is_a_failure_not_a_task(self) -> None:
        store = Mock(spec=GovernedTaskStore)
        store.mint_from_upstream.side_effect = GovernedTaskStore.mint_from_upstream
        store.relay_and_govern.side_effect = RuntimeError("register failed")

        with _with_store(store):
            result, created = govern_flat_call(_captured_task())

        assert not result.success
        assert result.error_type == "TaskRelayRegistrationFailed"
        assert created is None

    def test_a_task_with_no_store_to_govern_it_is_refused(self) -> None:
        with _with_store(None):
            result, created = govern_flat_call(_captured_task())

        assert not result.success
        assert result.error_type == "TaskRelayNotSupported"
        assert created is None

    def test_a_result_that_is_not_a_task_is_left_alone(self) -> None:
        plain = CallResult(index=0, call_id="call-1", success=True, result={"content": []})

        with _with_store(GovernedTaskStore()):
            result, created = govern_flat_call([plain])

        assert result is plain
        assert created is None

    def test_a_task_shaped_result_nobody_captured_is_not_made_a_task(self) -> None:
        """The decision is the worker's capture, never the upstream's shape."""
        uncaptured = CallResult(index=0, call_id="call-1", success=True, result={"task": dict(_TASK)})

        with _with_store(GovernedTaskStore()):
            result, created = govern_flat_call([uncaptured])

        assert result is uncaptured
        assert created is None
