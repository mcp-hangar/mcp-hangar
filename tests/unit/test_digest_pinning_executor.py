"""Executor call-path tests for per-tenant digest pinning (#233 / #278).

Mirrors the withdrawal call-path harness (test_tool_withdrawal.py). These exercise
the actual BatchExecutor pin block: block -> reject, warn -> pass, the
withdrawal-over-pin precedence, the per-tenant DigestMismatchEvent, and the
per-server enforcement scope.
"""

from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.events import DigestMismatchEvent
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.domain.value_objects import DigestEnforcement, ToolDigest
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec

_SERVER = "server_a"
_TOOL = "read_item"
_TENANT_A = "tenant:A"
_STALE = "a" * 64  # never matches the real schema digest


def _make_tool(name: str = _TOOL) -> ToolSchema:
    return ToolSchema(name=name, description="A tool", input_schema={"type": "object", "properties": {}})


def _identity(tenant_id: str | None) -> IdentityContext:
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
    ctx.get_mcp_server.return_value = Mock(
        state=Mock(value="ready"), has_tools=False, health=Mock(should_degrade=Mock(return_value=False))
    )
    ctx.mcp_server_exists.return_value = True
    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
        patch("mcp_hangar.server.tools.batch.validator.GROUPS") as val_groups,
    ):
        exec_groups.get.return_value = None
        val_groups.get.return_value = None
        yield ctx


def _execute(tenant_id: str | None, tool: str = _TOOL):
    token = identity_context_var.set(_identity(tenant_id))
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


def _mismatch_events(ctx):
    return [c.args[0] for c in ctx.event_bus.publish.call_args_list if isinstance(c.args[0], DigestMismatchEvent)]


class TestDigestPinExecutor:
    def test_stale_pin_blocks_under_block_mode(self, mock_context):
        registry = get_tool_projection_registry()
        registry.build_from_tools(_SERVER, [_make_tool()])
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, ToolDigest(tool_name=_TOOL, sha256=_STALE))
        # default enforcement is block

        result = _execute(_TENANT_A)
        assert result.results[0].success is False
        assert result.results[0].error_type == "ToolDigestMismatchError"
        mock_context.command_bus.send.assert_not_called()

        events = _mismatch_events(mock_context)
        assert len(events) == 1
        assert events[0].tenant_id == _TENANT_A  # per-tenant audit dimension (#278)
        assert events[0].mcp_server_id == _SERVER

    def test_warn_mode_emits_event_but_proceeds(self, mock_context):
        registry = get_tool_projection_registry()
        registry.build_from_tools(_SERVER, [_make_tool()])
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, ToolDigest(tool_name=_TOOL, sha256=_STALE))
        registry.set_digest_enforcement(_SERVER, DigestEnforcement.WARN)

        result = _execute(_TENANT_A)
        assert result.results[0].success is True
        mock_context.command_bus.send.assert_called_once()
        assert len(_mismatch_events(mock_context)) == 1  # audited, not blocked

    def test_unpinned_tenant_unaffected(self, mock_context):
        registry = get_tool_projection_registry()
        registry.build_from_tools(_SERVER, [_make_tool()])
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, ToolDigest(tool_name=_TOOL, sha256=_STALE))

        result = _execute("tenant:B")  # no pin for B
        assert result.results[0].success is True
        mock_context.command_bus.send.assert_called_once()
        assert _mismatch_events(mock_context) == []

    def test_withdrawal_takes_precedence_over_pin(self, mock_context):
        registry = get_tool_projection_registry()
        registry.build_from_tools(_SERVER, [_make_tool()], tenant_overrides={_TOOL: {_TENANT_A: "withdrawn"}})
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, ToolDigest(tool_name=_TOOL, sha256=_STALE))

        result = _execute(_TENANT_A)
        assert result.results[0].success is False
        assert result.results[0].error_type == "ToolWithdrawnError"  # withdrawal wins, not digest
        assert _mismatch_events(mock_context) == []  # no mismatch event for a withdrawn+pinned tool

    def test_per_server_enforcement_does_not_leak(self, mock_context):
        """An audit setting on another server must not downgrade this server's block (#278)."""
        registry = get_tool_projection_registry()
        registry.build_from_tools(_SERVER, [_make_tool()])
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, ToolDigest(tool_name=_TOOL, sha256=_STALE))
        registry.set_digest_enforcement("other_server", DigestEnforcement.AUDIT)  # different server

        result = _execute(_TENANT_A)
        assert result.results[0].success is False  # still blocked on server_a
        assert result.results[0].error_type == "ToolDigestMismatchError"
