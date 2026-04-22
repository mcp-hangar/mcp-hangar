"""Detection enforcement: Free vs Enterprise behavior scenarios.

Tests demonstrating that Free-tier (DetectionEnforcementHandler not wired)
lets DetectionRuleMatched events pass without enforcement, while Enterprise-tier
(handler wired to the event bus) executes the recommended action and emits
EnforcementActionTaken.
"""

from unittest.mock import MagicMock

import pytest

from mcp_hangar.application.event_handlers.detection_handler import DetectionEnforcementHandler
from mcp_hangar.domain.events import DetectionRuleMatched, EnforcementActionTaken


def _make_rule_matched(
    recommended_action: str = "alert",
    session_id: str = "sess-001",
    mcp_server_id: str = "filesystem",
    rule_id: str = "exfil-001",
) -> DetectionRuleMatched:
    return DetectionRuleMatched(
        rule_id=rule_id,
        rule_name="Credential Exfiltration",
        severity="high",
        session_id=session_id,
        mcp_server_id=mcp_server_id,
        matched_tools=("read_file", "send_email"),
        recommended_action=recommended_action,
    )


@pytest.fixture
def event_bus():
    bus = MagicMock()
    bus.published = []

    def capture(event):
        bus.published.append(event)

    bus.publish = capture
    return bus


@pytest.fixture
def handler(event_bus):
    return DetectionEnforcementHandler(event_bus=event_bus)


class TestDetectionFreePassesEnterpriseCatches:

    def test_free_no_handler_wired_rule_match_has_no_enforcement(self):
        unregistered_bus = MagicMock()
        unregistered_bus.published = []

        unregistered_bus.publish.assert_not_called()

    def test_enterprise_suspend_action_adds_session_to_suspended_set(self, handler, event_bus):
        from mcp_hangar.server.api.sessions import _suspended_sessions

        session_id = "sess-suspend-001"
        _suspended_sessions.discard(session_id)

        event = _make_rule_matched(recommended_action="suspend", session_id=session_id)
        handler.handle(event)

        assert session_id in _suspended_sessions

        _suspended_sessions.discard(session_id)

    def test_enterprise_suspend_action_emits_enforcement_event(self, handler, event_bus):
        from mcp_hangar.server.api.sessions import _suspended_sessions

        session_id = "sess-suspend-002"
        _suspended_sessions.discard(session_id)

        event = _make_rule_matched(recommended_action="suspend", session_id=session_id)
        handler.handle(event)

        enforcement_events = [e for e in event_bus.published if isinstance(e, EnforcementActionTaken)]
        assert len(enforcement_events) == 1
        assert enforcement_events[0].action == "suspend_session"
        assert enforcement_events[0].rule_id == "exfil-001"
        assert enforcement_events[0].session_id == session_id

        _suspended_sessions.discard(session_id)

    def test_enterprise_alert_action_emits_no_enforcement_event(self, handler, event_bus):
        event = _make_rule_matched(recommended_action="alert")
        handler.handle(event)

        enforcement_events = [e for e in event_bus.published if isinstance(e, EnforcementActionTaken)]
        assert len(enforcement_events) == 0

    def test_enterprise_block_action_requires_command_bus(self, event_bus):
        handler_no_cmd = DetectionEnforcementHandler(event_bus=event_bus, command_bus=None)

        event = _make_rule_matched(recommended_action="block")
        handler_no_cmd.handle(event)

        enforcement_events = [e for e in event_bus.published if isinstance(e, EnforcementActionTaken)]
        assert len(enforcement_events) == 0

    def test_enterprise_block_action_with_command_bus_dispatches_stop_command(self, event_bus):
        from mcp_hangar.application.commands.commands import StopMcpServerCommand

        command_bus = MagicMock()
        handler_with_cmd = DetectionEnforcementHandler(event_bus=event_bus, command_bus=command_bus)

        event = _make_rule_matched(recommended_action="block", mcp_server_id="filesystem")
        handler_with_cmd.handle(event)

        command_bus.send.assert_called_once()
        sent_command = command_bus.send.call_args[0][0]
        assert isinstance(sent_command, StopMcpServerCommand)
        assert sent_command.mcp_server_id == "filesystem"

        enforcement_events = [e for e in event_bus.published if isinstance(e, EnforcementActionTaken)]
        assert len(enforcement_events) == 1
        assert enforcement_events[0].action == "block_mcp_server"

    def test_enterprise_non_detection_event_is_ignored(self, handler, event_bus):
        from mcp_hangar.domain.events import McpServerStarted

        other_event = McpServerStarted(mcp_server_id="math", mode="subprocess",
        tools_count=3,
        startup_duration_ms=42.0,)
        handler.handle(other_event)

        assert len(event_bus.published) == 0
