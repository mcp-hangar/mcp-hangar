"""A partial tool-access-policy update must not remove the consent gate.

``SetToolAccessPolicyCommand`` carries only ``allow_list`` and ``deny_list``,
but ``ToolAccessPolicy`` also holds ``approval_list``,
``approval_timeout_seconds`` and ``approval_channel``. Rebuilding the policy
from the command alone therefore dropped the approval fields, and because
``BatchExecutor._check_approval_gate`` reads the same resolver singleton, a
plain "add one deny pattern" REST call silently un-gated every tool that
required human approval.

The invariant under test: an update may narrow access, and may replace the
allow/deny lists, but may never remove a consent requirement as a side effect.
"""

from unittest.mock import Mock

from mcp_hangar.auth.commands.commands import SetToolAccessPolicyCommand
from mcp_hangar.auth.commands.handlers import SetToolAccessPolicyHandler
from mcp_hangar.domain.services.tool_access_resolver import ToolAccessResolver
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy


GATED = ToolAccessPolicy(
    allow_list=("read_*",),
    deny_list=("drop_table",),
    approval_list=("wire_transfer", "delete_*"),
    approval_timeout_seconds=900,
    approval_channel="dashboard",
)


def _handle(resolver: ToolAccessResolver, command: SetToolAccessPolicyCommand) -> None:
    """Run the handler against a real resolver instance."""
    import mcp_hangar.domain.services.tool_access_resolver as resolver_module

    original = resolver_module.get_tool_access_resolver
    resolver_module.get_tool_access_resolver = lambda: resolver
    try:
        SetToolAccessPolicyHandler(Mock(), event_bus=Mock()).handle(command)
    finally:
        resolver_module.get_tool_access_resolver = original


class TestApprovalGateSurvivesPartialUpdate:
    def test_provider_scope_keeps_approval_list(self):
        resolver = ToolAccessResolver()
        resolver.set_mcp_server_policy("payments", GATED)

        _handle(
            resolver,
            SetToolAccessPolicyCommand(
                scope="provider",
                target_id="payments",
                allow_list=["read_*", "list_*"],
                deny_list=["drop_table"],
            ),
        )

        updated = resolver.get_configured_policy("provider", "payments")
        assert updated is not None
        assert updated.approval_list == ("wire_transfer", "delete_*")
        assert updated.approval_timeout_seconds == 900
        # The part the caller did restate is applied.
        assert updated.allow_list == ("read_*", "list_*")

    def test_group_scope_keeps_approval_list(self):
        resolver = ToolAccessResolver()
        resolver.set_group_policy("finance", GATED)

        _handle(
            resolver,
            SetToolAccessPolicyCommand(
                scope="group",
                target_id="finance",
                allow_list=["read_*"],
                deny_list=[],
            ),
        )

        updated = resolver.get_configured_policy("group", "finance")
        assert updated is not None
        assert updated.approval_list == ("wire_transfer", "delete_*")

    def test_target_without_prior_policy_gets_no_phantom_gate(self):
        """Preservation must not invent an approval list where none existed."""
        resolver = ToolAccessResolver()

        _handle(
            resolver,
            SetToolAccessPolicyCommand(
                scope="provider",
                target_id="fresh",
                allow_list=["read_*"],
                deny_list=[],
            ),
        )

        updated = resolver.get_configured_policy("provider", "fresh")
        assert updated is not None
        assert updated.approval_list == ()
        assert updated.approval_timeout_seconds == 300
        assert updated.approval_channel == "dashboard"


class TestConfiguredPolicyReadAccessor:
    def test_returns_none_for_unknown_target(self):
        resolver = ToolAccessResolver()
        assert resolver.get_configured_policy("provider", "nope") is None
        assert resolver.get_configured_policy("group", "nope") is None
        assert resolver.get_configured_policy("member", "g:m") is None
        assert resolver.get_configured_policy("nonsense-scope", "x") is None

    def test_mcp_server_scope_alias(self):
        resolver = ToolAccessResolver()
        resolver.set_mcp_server_policy("payments", GATED)
        assert resolver.get_configured_policy("mcp_server", "payments") == GATED
        assert resolver.get_configured_policy("provider", "payments") == GATED
