"""Changing an egress policy must leave a trace in the audit stream.

``SetL7PolicyHandler`` took an ``event_bus`` in its constructor and never
published to it, so the only record that enforcement had been narrowed or
widened was a log line -- while every sibling handler in the same module emits
a domain event. An enforcement plane whose own control changes are unaudited
cannot answer "who turned this off, and when".

The events carry a SUMMARY of the policy rather than its body: an auditor needs
to see that enforcement moved and in which direction, and the rule set can be
large and is retrievable from the server anyway.
"""

from unittest.mock import Mock

import pytest

from mcp_hangar.application.commands.crud_commands import SetL7PolicyCommand
from mcp_hangar.application.commands.crud_handlers import SetL7PolicyHandler
from mcp_hangar.domain.events import EgressPolicyCleared, EgressPolicySet
from mcp_hangar.domain.exceptions import McpServerNotFoundError
from mcp_hangar.domain.policies.egress_l7 import L7Policy


def _handler():
    repository = Mock()
    repository.get.return_value = Mock()
    event_bus = Mock()
    return SetL7PolicyHandler(repository, event_bus=event_bus), event_bus


def _policy(**overrides) -> L7Policy:
    data = {
        "tools": {"allow": ["read_*", "list_*"], "deny": ["drop_*"], "requireApproval": ["transfer"]},
        "arguments": {"secretPatterns": ["jwt"], "maxPayloadBytes": 262144},
        "defaultAction": "Deny",
        "mode": "Enforce",
    }
    data.update(overrides)
    return L7Policy.from_dict(data)


class TestAttachingAPolicyIsAudited:
    def test_publishes_egress_policy_set(self):
        handler, bus = _handler()
        handler.handle(SetL7PolicyCommand(mcp_server_id="srv1", policy=_policy(), source="operator"))

        bus.publish.assert_called_once()
        event = bus.publish.call_args[0][0]
        assert isinstance(event, EgressPolicySet)
        assert event.mcp_server_id == "srv1"
        assert event.source == "operator"

    def test_event_summarises_the_policy(self):
        handler, bus = _handler()
        handler.handle(SetL7PolicyCommand(mcp_server_id="srv1", policy=_policy(), source="api"))

        event = bus.publish.call_args[0][0]
        assert event.mode == "Enforce"
        assert event.default_action == "deny"
        assert event.allow_rules == 2
        assert event.deny_rules == 1
        assert event.require_approval_rules == 1
        assert event.secret_pattern_groups == ["jwt"]
        assert event.max_payload_bytes == 262144

    def test_audit_mode_is_visible_in_the_event(self):
        """Audit vs Enforce is the difference between watching and blocking.

        Values are carried verbatim from the enums, so mode keeps the CRD's
        capitalisation while default_action stays lower-case.
        """
        handler, bus = _handler()
        handler.handle(SetL7PolicyCommand(mcp_server_id="srv1", policy=_policy(mode="Audit"), source="operator"))

        assert bus.publish.call_args[0][0].mode == "Audit"

    def test_a_permissive_default_is_visible_in_the_event(self):
        handler, bus = _handler()
        handler.handle(SetL7PolicyCommand(mcp_server_id="srv1", policy=_policy(defaultAction="Allow"), source="api"))

        assert bus.publish.call_args[0][0].default_action == "allow"


class TestClearingAPolicyIsAudited:
    def test_publishes_egress_policy_cleared(self):
        """Clearing widens what the server may do -- its own event, not a flag."""
        handler, bus = _handler()
        handler.handle(SetL7PolicyCommand(mcp_server_id="srv1", policy=None, source="api"))

        bus.publish.assert_called_once()
        event = bus.publish.call_args[0][0]
        assert isinstance(event, EgressPolicyCleared)
        assert event.mcp_server_id == "srv1"
        assert event.source == "api"


class TestNothingIsPublishedOnFailure:
    def test_missing_server_publishes_no_event(self):
        """An audit record for a change that did not happen is worse than none."""
        repository = Mock()
        repository.get.return_value = None
        bus = Mock()
        handler = SetL7PolicyHandler(repository, event_bus=bus)

        with pytest.raises(McpServerNotFoundError):
            handler.handle(SetL7PolicyCommand(mcp_server_id="gone", policy=_policy()))

        bus.publish.assert_not_called()
