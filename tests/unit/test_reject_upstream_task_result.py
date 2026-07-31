"""Reject upstream MCP task handles on the invoke path (relay-only, ADR-008).

Hangar does not yet relay or govern task results. If an upstream ``tools/call``
returns a ``CreateTaskResult`` (a task handle) instead of a normal content
result, passing it through leaves the client with a handle for a task Hangar
never tracked -- its ``tasks/get`` follow-up would fail with "Task not found".
These tests pin the deliberate, clean rejection: the pure shape detector and the
executor call-path outcome.
"""

from unittest.mock import Mock, patch

import pytest

from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec
from mcp_hangar.server.tools.batch.executor import _is_task_result

_SERVER = "server_a"
_TOOL = "long_running_op"

_TASK_RESULT = {"task": {"taskId": "t1", "status": "working"}}
_CONTENT_RESULT = {"content": [{"type": "text", "text": "hello"}]}


def _identity(tenant_id: str | None) -> IdentityContext:
    return IdentityContext(
        caller=CallerIdentity(
            user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant_id
        )
    )


@pytest.fixture(autouse=True)
def reset_singletons():
    reset_tool_access_resolver()
    yield
    reset_tool_access_resolver()


@pytest.fixture()
def mock_context():
    ctx = Mock()
    ctx.event_bus = Mock()
    ctx.command_bus = Mock()
    ctx.get_mcp_server.return_value = Mock(
        state=Mock(value="ready"), has_tools=False, health=Mock(should_degrade=Mock(return_value=False))
    )
    ctx.mcp_server_exists.return_value = True
    # Kill-switch OFF (default): the factory wires governed_task_store only when
    # relay_tasks_enabled is on, so its absence == the relay-only stance. A bare
    # Mock would auto-create a truthy attribute, so pin it to None explicitly.
    ctx.governed_task_store = None
    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
    ):
        exec_groups.get.return_value = None
        yield ctx


def _execute(ctx, upstream_result: dict, tool: str = _TOOL):
    ctx.command_bus.send.return_value = upstream_result
    token = identity_context_var.set(_identity(None))
    try:
        return BatchExecutor().execute(
            batch_id="b",
            calls=[CallSpec(index=0, call_id="test-call", mcp_server=_SERVER, tool=tool, arguments={})],
            max_concurrency=1,
            global_timeout=30.0,
            fail_fast=False,
        )
    finally:
        identity_context_var.reset(token)


class TestIsTaskResult:
    def test_task_shaped_dict_is_detected(self):
        assert _is_task_result(_TASK_RESULT) is True

    def test_task_with_only_status_is_detected(self):
        assert _is_task_result({"task": {"status": "completed"}}) is True

    def test_normal_content_result_is_not_a_task(self):
        assert _is_task_result(_CONTENT_RESULT) is False

    @pytest.mark.parametrize(
        "junk",
        [
            {},
            {"task": "not-a-dict"},
            {"task": {}},
            {"task": None},
            {"foo": "bar"},
        ],
    )
    def test_junk_is_not_a_task(self, junk):
        assert _is_task_result(junk) is False


class TestRejectUpstreamTaskResult:
    def test_task_result_is_rejected_not_success(self, mock_context):
        result = _execute(mock_context, _TASK_RESULT)

        call = result.results[0]
        assert call.success is False
        assert call.error_type == "TaskRelayNotSupported"
        assert "task handle" in (call.error or "")
        # The dead handle is not passed back to the client.
        assert call.result is None
        # Whole batch reflects the rejection (not treated as success).
        assert result.success is False
        assert result.succeeded == 0
        # The upstream WAS invoked -- we reject the relay, we do not skip the call.
        mock_context.command_bus.send.assert_called_once()

    def test_normal_content_result_passes_through(self, mock_context):
        result = _execute(mock_context, _CONTENT_RESULT)

        call = result.results[0]
        assert call.success is True
        assert call.error_type is None
        assert call.result == _CONTENT_RESULT
        assert result.success is True
