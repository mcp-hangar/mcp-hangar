"""Enforcement handler for detection rule matches.

Executes local response actions (suspend_session, block_provider) in
reaction to DetectionRuleMatched events and emits EnforcementActionTaken.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from ...application.commands.commands import StopProviderCommand
from ...application.ports import ICommandBus
from ...domain.contracts.event_bus import IEventBus
from ...domain.events import DetectionRuleMatched, DomainEvent, EnforcementActionTaken
from ...logging_config import get_logger

logger = get_logger(__name__)


class DetectionEnforcementHandler:
    """Execute local enforcement actions for detection rule matches."""

    def __init__(self, event_bus: IEventBus, command_bus: ICommandBus | None = None) -> None:
        self._event_bus: IEventBus = event_bus
        self._command_bus: ICommandBus | None = command_bus

    def handle(self, event: DomainEvent) -> None:
        """Handle a detection match without letting failures escape."""
        try:
            if not isinstance(event, DetectionRuleMatched):
                return

            if event.recommended_action == "suspend":
                self._suspend_session(event.session_id, event.rule_id)
                self._event_bus.publish(
                    EnforcementActionTaken(
                        action="suspend_session",
                        rule_id=event.rule_id,
                        session_id=event.session_id,
                        provider_id=event.provider_id,
                        matched_tools=event.matched_tools,
                        detail=f"session {event.session_id} suspended by rule {event.rule_id}",
                    )
                )
                return

            if event.recommended_action == "block":
                self._block_provider(event.provider_id)
                self._event_bus.publish(
                    EnforcementActionTaken(
                        action="block_provider",
                        rule_id=event.rule_id,
                        session_id=event.session_id,
                        provider_id=event.provider_id,
                        matched_tools=event.matched_tools,
                        detail=f"provider {event.provider_id} blocked by rule {event.rule_id}",
                    )
                )
        except Exception as exc:  # noqa: BLE001 -- fault barrier for event bus handler
            logger.exception("detection_enforcement_handler_error", error=str(exc))

    def _suspend_session(self, session_id: str, rule_id: str) -> None:
        from ...server.api.sessions import _sessions_lock, _suspended_sessions

        with _sessions_lock:
            _suspended_sessions.add(session_id)

        logger.info("enforcement_session_suspended", session_id=session_id, rule_id=rule_id)

    def _block_provider(self, provider_id: str) -> None:
        if self._command_bus is None:
            raise RuntimeError("command bus required for block enforcement")

        command = StopProviderCommand(provider_id=provider_id, reason="detection_enforcement:block")
        self._command_bus.send(command)
