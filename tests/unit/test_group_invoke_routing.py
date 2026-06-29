"""Group invocation routing on the call path (#275 foundation).

Before this, a `hangar_call` targeting a group dispatched the GROUP id straight
to InvokeToolHandler, which cannot resolve it (groups live in GROUPS, not the
server repository) -> McpServerNotFoundError. The executor now selects a member
via the group's load-balancer and dispatches to that member.
"""

from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.commands import InvokeToolCommand
from mcp_hangar.application.read_models.tool_projection import reset_tool_projection_registry
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec

_GROUP = "llm-group"
_MEMBER = "llm-v1"
_TOOL = "generate"


def _member(server_id: str = _MEMBER):
    m = Mock()
    m.id = Mock(value=server_id)
    m.state = Mock(value="ready")  # not cold -> no cold-start path
    m.health = Mock(should_degrade=Mock(return_value=False))
    return m


def _identity(tenant_id=None):
    return IdentityContext(
        caller=CallerIdentity(
            user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant_id
        )
    )


@pytest.fixture(autouse=True)
def reset_singletons():
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()


@pytest.fixture()
def mock_context():
    ctx = Mock()
    ctx.event_bus = Mock()
    ctx.command_bus = Mock()
    ctx.command_bus.send.return_value = {"ok": True}
    ctx.get_mcp_server.return_value = None  # a group is NOT in the server repository
    ctx.mcp_server_exists.return_value = False
    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
        patch("mcp_hangar.server.tools.batch.validator.GROUPS") as val_groups,
    ):
        yield ctx, exec_groups, val_groups


def _execute(tenant_id=None):
    token = identity_context_var.set(_identity(tenant_id))
    try:
        return BatchExecutor().execute(
            batch_id="b",
            calls=[CallSpec(index=0, call_id="c1", mcp_server=_GROUP, tool=_TOOL, arguments={})],
            max_concurrency=1,
            global_timeout=30.0,
            fail_fast=False,
        )
    finally:
        identity_context_var.reset(token)


def _sent_invoke_commands(ctx):
    return [c.args[0] for c in ctx.command_bus.send.call_args_list if isinstance(c.args[0], InvokeToolCommand)]


class TestGroupInvokeRouting:
    def test_group_call_dispatches_to_selected_member(self, mock_context):
        ctx, exec_groups, val_groups = mock_context
        group = Mock()
        group.select_member.return_value = _member(_MEMBER)
        exec_groups.get.return_value = group
        val_groups.get.return_value = group

        result = _execute()

        assert result.results[0].success is True
        group.select_member.assert_called()  # member selection happened on the invoke path
        commands = _sent_invoke_commands(ctx)
        assert len(commands) == 1
        # Dispatched to the MEMBER id, not the group id.
        assert commands[0].mcp_server_id == _MEMBER
        assert commands[0].tool_name == _TOOL

    def test_group_with_no_available_member_fails_cleanly(self, mock_context):
        ctx, exec_groups, val_groups = mock_context
        group = Mock()
        group.select_member.return_value = None  # all members out of rotation / circuit open
        exec_groups.get.return_value = group
        val_groups.get.return_value = group

        result = _execute()

        assert result.results[0].success is False
        assert result.results[0].error_type == "NoAvailableMemberError"
        assert _sent_invoke_commands(ctx) == []  # never dispatched
