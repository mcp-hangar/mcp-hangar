"""Unit tests for detection enforcement wiring."""

# pyright: reportPrivateUsage=false, reportImplicitOverride=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingParameterType=false

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.commands.commands import StopProviderCommand
from mcp_hangar.application.event_handlers import DetectionEnforcementHandler
from mcp_hangar.application.ports import ICommandBus
from mcp_hangar.domain.contracts.event_bus import IEventBus
from mcp_hangar.domain.events import DetectionRuleMatched, EnforcementActionTaken
from mcp_hangar.server.api.sessions import _suspended_sessions, is_session_suspended


class RecordingEventBus(IEventBus):
    """Minimal event bus stub that records published events."""

    def __init__(self) -> None:
        self.published: list[object] = []

    def publish(self, event: object) -> None:
        self.published.append(event)


class RecordingCommandBus(ICommandBus):
    """Command bus stub that records sent commands."""

    def __init__(self) -> None:
        self.commands: list[object] = []

    def register(self, command_type: type, handler: object) -> None:
        del command_type, handler

    def send(self, command: object) -> dict[str, bool]:
        self.commands.append(command)
        return {"ok": True}


@pytest.fixture(autouse=True)
def clear_suspended_sessions():
    """Reset the in-memory suspension registry between tests."""
    _suspended_sessions.clear()
    yield
    _suspended_sessions.clear()


@pytest.fixture
def api_client():
    """API client for session suspension endpoint tests."""
    from mcp_hangar.server.api import create_api_router

    client = TestClient(create_api_router(), raise_server_exceptions=False)
    yield client


def _detection_event(action: str) -> DetectionRuleMatched:
    return DetectionRuleMatched(
        rule_id="rule-1",
        rule_name="Credential exfiltration",
        severity="critical",
        session_id="session-123",
        provider_id="provider-456",
        matched_tools=("read", "webfetch"),
        recommended_action=action,
    )


class TestDetectionEnforcementHandler:
    """Tests for DetectionEnforcementHandler."""

    def test_suspend_action_updates_registry_and_publishes_event(self):
        event_bus = RecordingEventBus()
        handler = DetectionEnforcementHandler(event_bus=event_bus)

        handler.handle(_detection_event("suspend"))

        assert is_session_suspended("session-123") is True
        assert len(event_bus.published) == 1
        published = event_bus.published[0]
        assert isinstance(published, EnforcementActionTaken)
        assert published.action == "suspend_session"
        assert published.rule_id == "rule-1"
        assert published.session_id == "session-123"

    def test_block_action_dispatches_stop_provider_and_publishes_event(self):
        event_bus = RecordingEventBus()
        command_bus = RecordingCommandBus()
        handler = DetectionEnforcementHandler(event_bus=event_bus, command_bus=command_bus)

        handler.handle(_detection_event("block"))

        assert len(command_bus.commands) == 1
        command = command_bus.commands[0]
        assert isinstance(command, StopProviderCommand)
        assert command.provider_id == "provider-456"
        assert command.reason == "detection_enforcement:block"
        assert len(event_bus.published) == 1
        published = event_bus.published[0]
        assert isinstance(published, EnforcementActionTaken)
        assert published.action == "block_provider"

    def test_alert_action_does_nothing(self):
        event_bus = RecordingEventBus()
        command_bus = RecordingCommandBus()
        handler = DetectionEnforcementHandler(event_bus=event_bus, command_bus=command_bus)

        handler.handle(_detection_event("alert"))

        assert command_bus.commands == []
        assert event_bus.published == []
        assert is_session_suspended("session-123") is False

    def test_handler_swallow_errors(self):
        event_bus = RecordingEventBus()
        handler = DetectionEnforcementHandler(event_bus=event_bus, command_bus=None)

        with patch("mcp_hangar.application.event_handlers.detection_handler.logger.exception") as log_exception:
            handler.handle(_detection_event("block"))

        log_exception.assert_called_once()
        assert event_bus.published == []


class TestSessionSuspensionEndpoint:
    """Tests for the session suspension API."""

    def test_suspend_endpoint_marks_session_suspended(self, api_client):
        response = api_client.post("/sessions/session-123/suspend", json={"reason": "rule-1"})

        assert response.status_code == 200
        assert response.json() == {"session_id": "session-123", "suspended": True}
        assert is_session_suspended("session-123") is True


class TestBlockProviderEndpoint:
    """Tests for the provider block API."""

    def test_block_endpoint_dispatches_detection_enforcement_stop(self):
        from mcp_hangar.server.api import create_api_router

        mock_context = Mock()
        mock_context.query_bus = Mock()
        mock_context.command_bus = Mock()
        mock_context.command_bus.send.return_value = {"status": "stopped", "provider": "provider-456"}

        with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
            client = TestClient(create_api_router(), raise_server_exceptions=False)
            response = client.post("/providers/provider-456/block", json={"reason": "ignored"})

        assert response.status_code == 200
        assert response.json() == {"provider_id": "provider-456", "blocked": True}
        command = mock_context.command_bus.send.call_args.args[0]
        assert isinstance(command, StopProviderCommand)
        assert command.provider_id == "provider-456"
        assert command.reason == "detection_enforcement:block"
