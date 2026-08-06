"""Enforcement handler for detection rule matches.

Executes local response actions (suspend_session, block_mcp_server) in
reaction to DetectionRuleMatched events and emits EnforcementActionTaken.
"""

from __future__ import annotations

from ...application.commands.commands import StopMcpServerCommand
from ...application.ports import ICommandBus
from ...domain.contracts.event_bus import IEventBus
from ...domain.contracts.session_suspension import ISessionSuspensionRegistry
from ...domain.events import DetectionRuleMatched, DomainEvent, EnforcementActionTaken, SessionSuspended
from ...logging_config import get_logger

logger = get_logger(__name__)


class DetectionEnforcementHandler:
    """Execute local enforcement actions for detection rule matches."""

    def __init__(
        self,
        event_bus: IEventBus,
        command_bus: ICommandBus | None = None,
        *,
        session_registry: ISessionSuspensionRegistry,
    ) -> None:
        """`session_registry` is required, deliberately.

        The obvious alternative -- default it to None and raise at suspend time
        -- makes forgotten wiring a runtime failure inside this handler's fault
        barrier, i.e. one log line and enforcement that silently does nothing.
        Required means a deployment that forgot it cannot start.
        """
        self._event_bus: IEventBus = event_bus
        self._command_bus: ICommandBus | None = command_bus
        self._session_registry: ISessionSuspensionRegistry = session_registry

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
                        mcp_server_id=event.mcp_server_id,
                        matched_tools=event.matched_tools,
                        detail=f"session {event.session_id} suspended by rule {event.rule_id}",
                    )
                )
                return

            if event.recommended_action == "block":
                self._block_mcp_server(event.mcp_server_id)
                self._event_bus.publish(
                    EnforcementActionTaken(
                        action="block_mcp_server",
                        rule_id=event.rule_id,
                        session_id=event.session_id,
                        mcp_server_id=event.mcp_server_id,
                        matched_tools=event.matched_tools,
                        detail=f"mcp_server {event.mcp_server_id} blocked by rule {event.rule_id}",
                    )
                )
        except Exception as exc:  # noqa: BLE001 -- fault barrier for event bus handler
            logger.exception("detection_enforcement_handler_error", error=str(exc))

    def _suspend_session(self, session_id: str, rule_id: str) -> None:
        """Suspend the session here, and announce it so peers suspend it too.

        Both, deliberately. Publishing alone would be tidier -- one way in, with
        the local registry updated by the projection like everyone else's -- and
        it has a failure mode this cannot afford: if the projection is not
        subscribed, the enforcement action silently does nothing at all. Applied
        first, the block always holds on this replica; the event is what carries
        it to the others. The projection re-applying it here is a no-op, because
        suspension is idempotent.
        """
        self._session_registry.suspend(session_id)
        self._event_bus.publish(
            SessionSuspended(session_id=session_id, reason=f"detection rule {rule_id}", source=rule_id)
        )

        logger.info("enforcement_session_suspended", session_id=session_id, rule_id=rule_id)

    def _block_mcp_server(self, mcp_server_id: str) -> None:
        if self._command_bus is None:
            raise RuntimeError("command bus required for block enforcement")

        command = StopMcpServerCommand(mcp_server_id=mcp_server_id, reason="detection_enforcement:block")
        self._command_bus.send(command)
