"""Audit records carry the caller the event's identity context names (#1342).

The handler used to read ``principal_id`` and ``roles``, keys
``IdentityContext.to_dict()`` never produces, and passed neither the user, the
session nor the tenant -- so no audit exporter, OTLP or compliance, ever saw
them. A failed call's duration was hard-coded to 0.0.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.application.event_handlers.audit_event_handler import OTLPAuditEventHandler
from mcp_hangar.compliance import CEFExporter, JSONLinesExporter, LEEFExporter, SyslogExporter
from mcp_hangar.domain.events import ToolInvocationCompleted, ToolInvocationFailed
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter
from mcp_hangar.observability.conventions import MCP, Caller


def _identity(**caller: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {"user_id": None, "agent_id": None, "session_id": None, **caller}
    return IdentityContext(caller=CallerIdentity(**fields), correlation_id="corr-1").to_dict()


USER = _identity(user_id="user-1", agent_id="agent-1", session_id="sess-1", principal_type="user", tenant_id="t-1")
AGENT_ONLY = _identity(agent_id="agent-2", session_id="sess-2", tenant_id="t-2")


def _exported(event: object) -> dict[str, Any]:
    exporter = MagicMock()
    OTLPAuditEventHandler(audit_exporter=exporter).handle(event)
    exporter.export_tool_invocation.assert_called_once()
    return dict(exporter.export_tool_invocation.call_args.kwargs)


def _completed(identity: dict[str, Any] | None) -> ToolInvocationCompleted:
    return ToolInvocationCompleted(mcp_server_id="math", tool_name="add", duration_ms=12.5, identity_context=identity)


def _failed(identity: dict[str, Any] | None) -> ToolInvocationFailed:
    return ToolInvocationFailed(
        mcp_server_id="math", tool_name="add", duration_ms=37.25, error_type="tool_error", identity_context=identity
    )


class TestTheHandlerReadsTheIdentityContext:
    @pytest.mark.parametrize("event", [_completed(USER), _failed(USER)], ids=["completed", "failed"])
    def test_a_user_caller_is_passed_with_session_and_tenant(self, event) -> None:
        kwargs = _exported(event)

        assert kwargs["caller_id"] == "user-1"  # the user, over the agent
        assert kwargs["caller_type"] == "user"
        assert kwargs["user_id"] == "user-1"
        assert kwargs["session_id"] == "sess-1"
        assert kwargs["tenant_id"] == "t-1"

    def test_without_a_user_the_agent_is_the_caller(self) -> None:
        kwargs = _exported(_completed(AGENT_ONLY))

        assert kwargs["caller_id"] == "agent-2"
        assert kwargs["caller_type"] == "anonymous"
        assert kwargs["user_id"] is None
        assert (kwargs["session_id"], kwargs["tenant_id"]) == ("sess-2", "t-2")

    def test_roles_are_not_invented(self) -> None:
        # IdentityContext carries no roles; nothing may be passed in their place.
        assert _exported(_completed(USER)).get("caller_roles") is None

    @pytest.mark.parametrize("event", [_completed(None), _failed(None)], ids=["completed", "failed"])
    def test_without_an_identity_no_caller_field_is_passed(self, event) -> None:
        kwargs = _exported(event)

        for key in ("caller_id", "caller_type", "user_id", "session_id", "tenant_id"):
            assert kwargs[key] is None, key

    def test_a_failed_call_carries_its_duration(self) -> None:
        assert _exported(_failed(USER))["duration_ms"] == 37.25


class TestTheOtlpRecordCarriesTheCaller:
    def test_the_record_carries_caller_session_and_tenant(self) -> None:
        exporter = OTLPAuditExporter()
        with patch.object(exporter, "_emit_log_record") as emit:
            OTLPAuditEventHandler(audit_exporter=exporter).handle(_completed(USER))

        attributes = emit.call_args.args[0]
        assert attributes[Caller.ID] == "user-1"
        assert attributes[Caller.TYPE] == "user"
        assert attributes[Caller.TENANT] == "t-1"
        assert attributes[MCP.USER_ID] == "user-1"
        assert attributes[MCP.SESSION_ID] == "sess-1"
        assert Caller.ROLES not in attributes

    def test_without_an_identity_the_record_has_no_caller_field(self) -> None:
        exporter = OTLPAuditExporter()
        with patch.object(exporter, "_emit_log_record") as emit:
            OTLPAuditEventHandler(audit_exporter=exporter).handle(_completed(None))

        attributes = emit.call_args.args[0]
        assert not {key for key in attributes if key.startswith("mcp.caller.")}
        assert MCP.USER_ID not in attributes and MCP.SESSION_ID not in attributes


def _json_fields(line: str) -> tuple[Any, Any]:
    payload = json.loads(line)
    return payload.get("user_id"), payload.get("session_id")


# format -> (exporter factory, the user field, the session field) as each renders them
COMPLIANCE: dict[str, tuple[Callable[..., Any], str, str]] = {
    "cef": (CEFExporter, "suser=user-1", "cs3=sess-1"),
    "leef": (LEEFExporter, "usrName=user-1", "sessID=sess-1"),
    "syslog": (SyslogExporter, 'user="user-1"', 'session="sess-1"'),
}


class TestComplianceOutputCarriesUserAndSession:
    @pytest.mark.parametrize("fmt", sorted(COMPLIANCE))
    @pytest.mark.parametrize("make_event", [_completed, _failed], ids=["completed", "failed"])
    def test_the_line_carries_user_and_session(self, fmt, make_event) -> None:
        factory, user_field, session_field = COMPLIANCE[fmt]
        lines: list[str] = []
        OTLPAuditEventHandler(audit_exporter=factory(output_fn=lines.append)).handle(make_event(USER))

        [line] = lines
        assert user_field in line and session_field in line, line

    @pytest.mark.parametrize("make_event", [_completed, _failed], ids=["completed", "failed"])
    def test_the_json_line_carries_user_and_session(self, make_event) -> None:
        lines: list[str] = []
        OTLPAuditEventHandler(audit_exporter=JSONLinesExporter(output_fn=lines.append)).handle(make_event(USER))

        [line] = lines
        assert _json_fields(line) == ("user-1", "sess-1")

    def test_without_an_identity_the_json_line_has_neither(self) -> None:
        lines: list[str] = []
        OTLPAuditEventHandler(audit_exporter=JSONLinesExporter(output_fn=lines.append)).handle(_completed(None))

        assert _json_fields(lines[0]) == (None, None)
