"""An armed approval gate must say when nobody is listening (#914).

A deployment could put a tool on `approval_list`, boot green, and have every
gated call sit for `approval_timeout_seconds` and then deny -- because the
delivery channel notified nobody. Nothing leaked: the gate is fail-closed by
expiry. But five minutes of hanging and then an error is indistinguishable from
a broken gateway, and the remediation reached for under that pressure is
emptying `approval_list` -- fail-closed in code, fail-open in the organisation.

Two things are asserted here:

* the startup check says so, at ERROR by default and as a refusal only when a
  deployment opts in with `approvals.delivery.required: true`; and
* `approval_channel`, declared per policy and merged with care across scopes,
  now actually selects a delivery instead of being a label on the request.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs

from mcp_hangar.approvals.bootstrap import (
    ApprovalDeliveryRouter,
    channel_reaches_a_human,
    configured_channel,
)
from mcp_hangar.approvals.delivery.event_stream import EventStreamApprovalDelivery
from mcp_hangar.approvals.delivery.noop import NoOpApprovalDelivery
from mcp_hangar.approvals.models import ApprovalRequest, ApprovalState
from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.services.tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.server.bootstrap.reachability import (
    check_subsystem_reachability,
    enforce_subsystem_reachability,
)

GATED = ToolAccessPolicy(approval_list=("refund_*",))


@pytest.fixture(autouse=True)
def _clean_resolver():
    reset_tool_access_resolver()
    yield
    reset_tool_access_resolver()


@pytest.fixture
def context_with_gate():
    return MagicMock(approval_gate=MagicMock(name="approval-gate"))


def _request(channel: str) -> ApprovalRequest:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    return ApprovalRequest(
        approval_id="a-1",
        provider_id="payments",
        tool_name="refund_payment",
        arguments={},
        arguments_hash="deadbeef",
        requested_at=now,
        expires_at=now + timedelta(seconds=300),
        state=ApprovalState.PENDING,
        channel=channel,
        correlation_id="c-1",
    )


class TestWhichChannelsReachAnyone:
    def test_the_event_stream_does(self):
        """Its notification rides the domain events /api/ws/events serves."""
        assert channel_reaches_a_human("event_stream") is True

    def test_the_retired_dashboard_name_resolves_to_one_that_does(self):
        assert channel_reaches_a_human("dashboard") is True

    def test_noop_does_not(self):
        assert channel_reaches_a_human("noop") is False

    def test_a_channel_no_package_claims_does_not(self):
        with patch("importlib.metadata.entry_points", return_value=[]):
            assert channel_reaches_a_human("slack") is False

    def test_an_installed_adapter_does(self):
        from types import SimpleNamespace

        entry_point = SimpleNamespace(name="slack", value="acme:factory", load=lambda: object)
        with patch("importlib.metadata.entry_points", return_value=[entry_point]):
            assert channel_reaches_a_human("slack") is True

    def test_no_approvals_config_at_all_reaches_nobody(self):
        assert configured_channel(None) == "noop"


class TestTheStartupCheckSaysSo:
    def test_a_gated_policy_on_a_silent_channel_is_reported(self, context_with_gate):
        get_tool_access_resolver().set_mcp_server_policy("payments", GATED)
        config = {"approvals": {"channel": "noop"}}

        unreachable = check_subsystem_reachability(config, context_with_gate)

        assert [r.subsystem for r in unreachable] == ["approval_delivery"]
        assert "mcp_server:payments" in unreachable[0].required_by
        assert "noop" in unreachable[0].required_by

    def test_it_does_not_refuse_the_boot_by_default(self, context_with_gate):
        """Fail-closed already; the missing thing is a signal, not enforcement."""
        get_tool_access_resolver().set_mcp_server_policy("payments", GATED)

        unreachable = enforce_subsystem_reachability({"approvals": {"channel": "noop"}}, context_with_gate)

        assert [r.subsystem for r in unreachable] == ["approval_delivery"]

    def test_it_is_logged_at_error_naming_the_scope_and_the_channel(self, context_with_gate):
        get_tool_access_resolver().set_mcp_server_policy("payments", GATED)

        with capture_logs() as logs:
            enforce_subsystem_reachability({"approvals": {"channel": "noop"}}, context_with_gate)

        errors = [e for e in logs if e.get("log_level") == "error"]
        assert any(e["event"] == "subsystem_configured_but_unreachable" for e in errors)
        assert any("payments" in str(e.get("required_by", "")) for e in errors)

    def test_opting_in_turns_it_into_a_refusal(self, context_with_gate):
        get_tool_access_resolver().set_mcp_server_policy("payments", GATED)
        config = {"approvals": {"channel": "noop", "delivery": {"required": True}}}

        with pytest.raises(ConfigurationError, match="approval_delivery"):
            enforce_subsystem_reachability(config, context_with_gate)

    def test_the_default_channel_notifies_so_nothing_is_reported(self, context_with_gate):
        get_tool_access_resolver().set_mcp_server_policy("payments", GATED)

        assert check_subsystem_reachability({}, context_with_gate) == []

    def test_an_ungated_policy_is_not_the_delivery_check_s_business(self, context_with_gate):
        get_tool_access_resolver().set_mcp_server_policy("billing", ToolAccessPolicy(deny_list=("delete_*",)))

        assert check_subsystem_reachability({"approvals": {"channel": "noop"}}, context_with_gate) == []

    def test_a_per_policy_channel_is_what_is_checked(self, context_with_gate):
        """The policy's channel, not the global one, is what that policy will use."""
        get_tool_access_resolver().set_mcp_server_policy(
            "payments", ToolAccessPolicy(approval_list=("refund_*",), approval_channel="slack")
        )

        with patch("importlib.metadata.entry_points", return_value=[]):
            unreachable = check_subsystem_reachability({}, context_with_gate)

        assert len(unreachable) == 1
        assert "slack" in unreachable[0].required_by

    def test_no_gate_service_leaves_this_check_silent(self):
        """That case is the other check's, and it refuses the boot on its own."""
        get_tool_access_resolver().set_mcp_server_policy("payments", GATED)
        context = MagicMock(approval_gate=None)

        subsystems = {r.subsystem for r in check_subsystem_reachability({"approvals": {"channel": "noop"}}, context)}
        assert subsystems == {"approval_gate"}


class TestApprovalChannelFinallyRoutes:
    """It was merged with care across policy narrowing and dispatched nowhere."""

    @pytest.mark.asyncio
    async def test_a_policy_channel_selects_its_own_delivery(self):
        built = []

        class _Adapter:
            def __init__(self, config):
                built.append(config)

            async def send(self, request):
                built.append(request.approval_id)

        from types import SimpleNamespace

        entry_point = SimpleNamespace(name="pigeon", value="acme:factory", load=lambda: _Adapter)
        with patch("importlib.metadata.entry_points", return_value=[entry_point]):
            router = ApprovalDeliveryRouter({"approvals": {"channel": "noop", "pigeon": {"loft": "north"}}})
            await router.send(_request("pigeon"))

        assert built == [{"loft": "north"}, "a-1"]

    @pytest.mark.asyncio
    async def test_an_approval_naming_no_channel_uses_the_global_one(self):
        router = ApprovalDeliveryRouter({"approvals": {"channel": "event_stream"}})

        assert isinstance(router._delivery_for(""), EventStreamApprovalDelivery)
        await router.send(_request(""))

    @pytest.mark.asyncio
    async def test_a_channel_nothing_claims_degrades_rather_than_raising(self):
        with patch("importlib.metadata.entry_points", return_value=[]):
            router = ApprovalDeliveryRouter({"approvals": {"channel": "event_stream"}})
            await router.send(_request("nobody-provides-this"))

            assert isinstance(router._delivery_for("nobody-provides-this"), NoOpApprovalDelivery)

    @pytest.mark.asyncio
    async def test_a_channel_first_seen_after_boot_is_still_routed(self):
        """A policy can arrive from a hot reload or over REST."""
        router = ApprovalDeliveryRouter({"approvals": {"channel": "noop"}})

        await router.send(_request("event_stream"))

        assert isinstance(router._delivery_for("event_stream"), EventStreamApprovalDelivery)

    def test_the_router_is_itself_a_delivery(self):
        """So the gate service calls send() and knows nothing about channels."""
        import inspect

        from mcp_hangar.approvals.delivery.base import ApprovalDelivery

        assert inspect.signature(ApprovalDeliveryRouter.send) == inspect.signature(ApprovalDelivery.send)
