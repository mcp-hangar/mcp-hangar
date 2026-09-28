"""OTLP audit event handler -- bridges domain events to IAuditExporter.

Subscribes to tool invocation, tool refusal and mcp_server state events. Forwards them
to IAuditExporter (OTLPAuditExporter in production, NullAuditExporter
when OTLP not configured).

MIT licensed -- part of core event handler infrastructure.
"""

from typing import Any

from ...domain.contracts.cost import ICostAttributor, InvocationContext, NullCostAttributor
from ...domain.events import (
    McpServerStateChanged,
    ToolCallRefused,
    ToolInvocationCompleted,
    ToolInvocationFailed,
)
from ...logging_config import get_logger
from ...observability.conventions import L7, Gate
from ..ports.observability import IAuditExporter, NullAuditExporter

logger = get_logger(__name__)


def _caller_fields(identity: dict[str, Any] | None) -> dict[str, Any]:
    """The exporter's caller arguments, from an event's ``IdentityContext.to_dict()``.

    The caller id is the user id, or the agent id when there is no user. Roles
    are not passed: ``IdentityContext`` carries ids, not roles, and the roles
    the authorizer resolved for the call are kept on neither the principal nor
    the event. No identity (auth off) yields no caller fields.
    """
    identity = identity or {}
    return {
        "user_id": identity.get("user_id"),
        "session_id": identity.get("session_id"),
        "tenant_id": identity.get("tenant_id"),
        "caller_type": identity.get("principal_type"),
        "caller_id": identity.get("user_id") or identity.get("agent_id"),
    }


def _route_fields(event: ToolInvocationCompleted | ToolInvocationFailed) -> tuple[str, str]:
    """The record's target pair (#1594): (the logical target, the invoked server as the backend).

    For a group call the event's server is the selected member, and the group
    is ``logical_target``. An event without one -- persisted before #1594, or a
    call that named its server -- was addressed to the server that ran it, so a
    standalone record carries the same value twice, as spans do.
    """
    return event.logical_target or event.mcp_server_id, event.mcp_server_id


def _refusal_fields(event: ToolCallRefused) -> dict[str, str]:
    """The bounded refusal attributes of *event*, keyed by the ADR-029 vocabulary; unset ones left out."""
    candidates = {
        Gate.NAME: event.gate,
        Gate.REASON: event.gate_reason,
        L7.VERDICT: event.l7_verdict,
        L7.MODE: event.l7_mode,
        L7.RULE_KIND: event.l7_rule_kind,
        L7.POLICY_ID: event.l7_policy_id,
    }
    return {key: value for key, value in candidates.items() if value}


class OTLPAuditEventHandler:
    """Forwards security-relevant domain events to the audit exporter.

    Designed to be registered with the event bus. Each handle() call
    is synchronous and completes before returning. Export failures are
    swallowed by the exporter (OTLPAuditExporter fault-barrier pattern).
    """

    def __init__(
        self,
        audit_exporter: IAuditExporter | None = None,
        cost_attributor: ICostAttributor | None = None,
    ) -> None:
        self._exporter = audit_exporter or NullAuditExporter()
        self._cost_attributor = cost_attributor or NullCostAttributor()

    def handle(self, event: object) -> None:
        if isinstance(event, ToolInvocationCompleted):
            cost_record = self._cost_attributor.compute_cost(
                InvocationContext(
                    mcp_server_id=event.mcp_server_id,
                    tool_name=event.tool_name,
                    duration_ms=event.duration_ms,
                    correlation_id=event.correlation_id,
                )
            )
            target, backend = _route_fields(event)
            self._exporter.export_tool_invocation(
                mcp_server_id=target,
                route_backend=backend,
                tool_name=event.tool_name,
                status="success",
                duration_ms=event.duration_ms,
                cost_cents=cost_record.cost_cents if cost_record.cost_cents else None,
                cost_model=str(cost_record.cost_model) if cost_record.cost_cents else None,
                cost_input_tokens=cost_record.input_tokens if cost_record.input_tokens else None,
                cost_output_tokens=cost_record.output_tokens if cost_record.output_tokens else None,
                **_caller_fields(event.identity_context),
            )
        elif isinstance(event, ToolInvocationFailed):
            target, backend = _route_fields(event)
            self._exporter.export_tool_invocation(
                mcp_server_id=target,
                route_backend=backend,
                tool_name=event.tool_name,
                status="error",
                duration_ms=event.duration_ms,
                error_type=event.error_type,
                **_caller_fields(event.identity_context),
            )
        elif isinstance(event, ToolCallRefused):
            # One record per refusal (#1582). The refusal events that already
            # exist -- ToolApprovalDenied, AuthorizationDenied,
            # EgressPolicyEnforced -- are not subscribed, so none counts twice.
            # Its server is already the logical target; the backend is set only
            # once the route was resolved (#1594).
            self._exporter.export_tool_invocation(
                mcp_server_id=event.mcp_server_id,
                route_backend=event.route_backend,
                tool_name=event.tool_name,
                status="denied",
                duration_ms=event.elapsed_ms,
                refusal=_refusal_fields(event),
                **_caller_fields(event.identity_context),
            )
        elif isinstance(event, McpServerStateChanged):
            self._exporter.export_mcp_server_state_change(
                mcp_server_id=event.mcp_server_id,
                from_state=event.old_state,
                to_state=event.new_state,
            )
