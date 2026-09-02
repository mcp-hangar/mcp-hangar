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


class TestDigestPinAtColdStart:
    """The catalogue only appears when the backend starts (#601).

    `McpServerStarted` populates the projection registry, and the executor's cold
    start happens AFTER the pin gate's original position. So on a freshly booted
    gateway the first call to a pinned tool found no projection and skipped the
    check entirely -- one unvalidated call per boot, per server, and gateway
    restarts are routine in Kubernetes.
    """

    @staticmethod
    def _cold_server_populating_catalogue_on_start(mock_context, registry, tool: ToolSchema):
        """Wire a cold backend whose start publishes the catalogue, as in production."""
        from mcp_hangar.application.commands import StartMcpServerCommand

        cold = Mock(
            state=Mock(value="cold"),
            has_tools=False,
            health=Mock(should_degrade=Mock(return_value=False)),
        )
        mock_context.get_mcp_server.return_value = cold

        def _send(command):
            if isinstance(command, StartMcpServerCommand):
                registry.build_from_tools(_SERVER, [tool])
                cold.state = Mock(value="ready")
            return {"ok": True}

        mock_context.command_bus.send.side_effect = _send
        return cold

    def test_stale_pin_blocks_on_the_first_call_after_boot(self, mock_context):
        """The regression: the first call must not slip past the pin."""
        from mcp_hangar.application.commands import InvokeToolCommand

        registry = get_tool_projection_registry()
        # Pin configured, catalogue NOT built -- exactly a just-booted gateway.
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, ToolDigest(tool_name=_TOOL, sha256=_STALE))
        self._cold_server_populating_catalogue_on_start(mock_context, registry, _make_tool())

        result = _execute(_TENANT_A)

        assert result.results[0].success is False
        assert result.results[0].error_type == "ToolDigestMismatchError"
        invokes = [c for c in mock_context.command_bus.send.call_args_list if isinstance(c.args[0], InvokeToolCommand)]
        assert invokes == [], "the backend was invoked despite a stale pin"

    def test_matching_pin_still_passes_on_the_first_call_after_boot(self, mock_context):
        """The other half: closing the hole must not deny honest first calls."""
        from mcp_hangar.domain.services.digest_computation import compute_tool_digest

        tool = _make_tool()
        honest = compute_tool_digest(tool.to_dict()).sha256

        registry = get_tool_projection_registry()
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, ToolDigest(tool_name=_TOOL, sha256=honest))
        self._cold_server_populating_catalogue_on_start(mock_context, registry, tool)

        result = _execute(_TENANT_A)

        assert result.results[0].success is True, result.results[0].error
        assert _mismatch_events(mock_context) == []

    def test_a_pinned_tool_that_never_appears_is_refused_under_block(self, mock_context):
        """Unverifiable is not the same as fine: no catalogue entry -> fail closed.

        Mirrors how the gate already treats a digest it cannot compute.
        """
        registry = get_tool_projection_registry()
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, ToolDigest(tool_name=_TOOL, sha256=_STALE))
        # Start publishes a catalogue that does NOT contain the pinned tool.
        self._cold_server_populating_catalogue_on_start(mock_context, registry, _make_tool("some_other_tool"))

        result = _execute(_TENANT_A)

        assert result.results[0].success is False
        assert result.results[0].error_type == "ToolDigestMismatchError"

    def test_an_unpinned_tool_is_unaffected_by_the_deferred_check(self, mock_context):
        registry = get_tool_projection_registry()
        self._cold_server_populating_catalogue_on_start(mock_context, registry, _make_tool())

        result = _execute(_TENANT_A)

        assert result.results[0].success is True, result.results[0].error


_GROUP = "pool"


class TestDigestPinAtColdStartThroughAGroup:
    """The same boot, with the call routed through a group (#1166).

    A group has two names and the registry is keyed by the one that STARTED,
    which is always a member. The pre-gate learned that in #1040; the deferred
    re-check after the cold start kept asking the group id alone, found nothing,
    and refused the call as unverifiable -- one spurious ToolDigestMismatchError
    per boot, per (group, pinned tool, tenant), on the path #601 was built for.
    """

    @staticmethod
    def _cold_member_of_a_group(mock_context, registry, tool: ToolSchema):
        """A group whose only member is cold and publishes its catalogue on start."""
        from mcp_hangar.application.commands import StartMcpServerCommand

        member = Mock(
            id=Mock(value=_SERVER),
            state=Mock(value="cold"),
            has_tools=False,
            health=Mock(should_degrade=Mock(return_value=False)),
        )
        group = Mock(select_member_for=Mock(return_value=member))

        # The group id resolves to no server -- that is what makes it a group.
        mock_context.get_mcp_server.side_effect = lambda server_id: None if server_id == _GROUP else member

        def _send(command):
            if isinstance(command, StartMcpServerCommand):
                registry.build_from_tools(_SERVER, [tool])
                member.state = Mock(value="ready")
            return {"ok": True}

        mock_context.command_bus.send.side_effect = _send
        return group

    def _execute_through_the_group(self, group, tenant_id: str | None):
        token = identity_context_var.set(_identity(tenant_id))
        try:
            with (
                patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
                patch("mcp_hangar.server.tools.batch.validator.GROUPS") as val_groups,
            ):
                exec_groups.get.side_effect = lambda server_id: group if server_id == _GROUP else None
                val_groups.get.side_effect = exec_groups.get.side_effect
                return BatchExecutor().execute(
                    batch_id="b",
                    calls=[CallSpec(index=0, call_id="test-call", mcp_server=_GROUP, tool=_TOOL, arguments={})],
                    max_concurrency=1,
                    global_timeout=30.0,
                    fail_fast=False,
                )
        finally:
            identity_context_var.reset(token)

    def test_a_matching_pin_passes_on_the_first_call_after_boot(self, mock_context):
        """The regression: this call used to be refused as unverifiable."""
        from mcp_hangar.domain.services.digest_computation import compute_tool_digest

        tool = _make_tool()
        honest = compute_tool_digest(tool.to_dict()).sha256

        registry = get_tool_projection_registry()
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, ToolDigest(tool_name=_TOOL, sha256=honest))
        group = self._cold_member_of_a_group(mock_context, registry, tool)

        result = self._execute_through_the_group(group, _TENANT_A)

        assert result.results[0].success is True, result.results[0].error
        assert _mismatch_events(mock_context) == []

    def test_a_stale_pin_on_the_member_still_blocks(self, mock_context):
        """Resolving the member must not turn the deferred check into a pass."""
        from mcp_hangar.application.commands import InvokeToolCommand

        registry = get_tool_projection_registry()
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, ToolDigest(tool_name=_TOOL, sha256=_STALE))
        group = self._cold_member_of_a_group(mock_context, registry, _make_tool())

        result = self._execute_through_the_group(group, _TENANT_A)

        assert result.results[0].success is False
        assert result.results[0].error_type == "ToolDigestMismatchError"
        invokes = [c for c in mock_context.command_bus.send.call_args_list if isinstance(c.args[0], InvokeToolCommand)]
        assert invokes == [], "the member was invoked despite a stale pin"

    def test_a_pinned_tool_that_never_appears_is_still_refused(self, mock_context):
        """Fail-closed is kept: unverifiable through a group is still unverifiable."""
        registry = get_tool_projection_registry()
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, ToolDigest(tool_name=_TOOL, sha256=_STALE))
        group = self._cold_member_of_a_group(mock_context, registry, _make_tool("some_other_tool"))

        result = self._execute_through_the_group(group, _TENANT_A)

        assert result.results[0].success is False
        assert result.results[0].error_type == "ToolDigestMismatchError"
