from collections.abc import Callable
from datetime import UTC, datetime
import json
from typing import Any

import pytest

from mcp_hangar.application.event_handlers.audit_event_handler import OTLPAuditEventHandler
from mcp_hangar.application.event_handlers.audit_handler import AuditRecord
from mcp_hangar.compliance import CEFExporter, JSONLinesExporter, LEEFExporter, SyslogExporter
from mcp_hangar.compliance.cef_formatter import format_audit_record
from mcp_hangar.compliance.jsonlines_exporter import _record_to_json_line
from mcp_hangar.compliance.leef_exporter import _format_record as _format_leef
from mcp_hangar.compliance.syslog_exporter import _format_record as _format_syslog
from mcp_hangar.domain.events import ToolInvocationCompleted
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext


def _single_line(lines: list[str]) -> str:
    assert len(lines) == 1
    return lines[0]


class TestCEFExporter:
    def test_minimal_tool_invocation(self) -> None:
        lines: list[str] = []
        exporter = CEFExporter(output_fn=lines.append)

        exporter.export_tool_invocation("srv-1", "echo", "success", 12.5)

        assert exporter.lines_exported == 1
        line = _single_line(lines)
        assert line.startswith("CEF:0|")
        assert "cs1=srv-1" in line
        assert "cs5=echo" in line
        assert "act=ToolInvocationCompleted" in line
        assert "cn1=12.5" in line

    def test_full_tool_invocation_with_caller_and_cost(self) -> None:
        lines: list[str] = []
        exporter = CEFExporter(output_fn=lines.append)

        exporter.export_tool_invocation(
            "srv-2",
            "analyze",
            "success",
            42.0,
            user_id="alice",
            session_id="sess-1",
            caller_type="agent",
            caller_id="agent-7",
            caller_roles="admin,ops",
            cost_cents=99,
            cost_model="gpt-4.1",
            cost_input_tokens=100,
            cost_output_tokens=25,
        )

        assert exporter.lines_exported == 1
        line = _single_line(lines)
        assert line.startswith("CEF:0|")
        assert "cs1=srv-2" in line
        assert "cs5=analyze" in line
        assert "suser=alice" in line
        assert "cs3=sess-1" in line
        assert "act=ToolInvocationCompleted" in line
        assert "cn1=42.0" in line

    def test_tool_failed_invocation(self) -> None:
        lines: list[str] = []
        exporter = CEFExporter(output_fn=lines.append)

        exporter.export_tool_invocation("srv-3", "fail", "error", 7.0, error_type="RuntimeError")

        assert exporter.lines_exported == 1
        line = _single_line(lines)
        assert line.startswith("CEF:0|")
        assert "cs1=srv-3" in line
        assert "cs5=fail" in line
        assert "act=ToolInvocationFailed" in line
        assert "reason=RuntimeError" in line

    def test_mcp_server_state_change(self) -> None:
        lines: list[str] = []
        exporter = CEFExporter(output_fn=lines.append)

        exporter.export_mcp_server_state_change("srv-4", "READY", "DEGRADED")

        assert exporter.lines_exported == 1
        line = _single_line(lines)
        assert line.startswith("CEF:0|")
        assert "cs1=srv-4" in line
        assert "Provider State Changed" in line
        assert "act=ProviderStateChanged" in line
        assert "cs1Label=ProviderID" in line


class TestLEEFExporter:
    def test_minimal_tool_invocation(self) -> None:
        lines: list[str] = []
        exporter = LEEFExporter(output_fn=lines.append)

        exporter.export_tool_invocation("srv-1", "echo", "success", 12.5)

        assert exporter.lines_exported == 1
        line = _single_line(lines)
        assert line.startswith("LEEF:2.0|")
        assert "\t" in line
        assert "action=echo" in line
        assert "duration=12.5" in line
        assert "src=srv-1" in line

    def test_full_tool_invocation_with_caller_and_cost(self) -> None:
        lines: list[str] = []
        exporter = LEEFExporter(output_fn=lines.append)

        exporter.export_tool_invocation(
            "srv-2",
            "analyze",
            "success",
            42.0,
            user_id="alice",
            session_id="sess-1",
            caller_type="agent",
            caller_id="agent-7",
            caller_roles="admin,ops",
            cost_cents=99,
            cost_model="gpt-4.1",
            cost_input_tokens=100,
            cost_output_tokens=25,
        )

        assert exporter.lines_exported == 1
        line = _single_line(lines)
        assert line.startswith("LEEF:2.0|")
        assert "action=analyze" in line
        assert "duration=42.0" in line
        assert "usrName=alice" in line
        assert "sessID=sess-1" in line
        assert "src=srv-2" in line

    def test_tool_failed_invocation(self) -> None:
        lines: list[str] = []
        exporter = LEEFExporter(output_fn=lines.append)

        exporter.export_tool_invocation("srv-3", "fail", "error", 7.0, error_type="RuntimeError")

        assert exporter.lines_exported == 1
        line = _single_line(lines)
        assert line.startswith("LEEF:2.0|")
        assert "reason=RuntimeError" in line
        assert "action=fail" in line
        assert "src=srv-3" in line

    def test_mcp_server_state_change(self) -> None:
        lines: list[str] = []
        exporter = LEEFExporter(output_fn=lines.append)

        exporter.export_mcp_server_state_change("srv-4", "READY", "DEGRADED")

        assert exporter.lines_exported == 1
        line = _single_line(lines)
        assert line.startswith("LEEF:2.0|")
        assert "oldState=READY" in line
        assert "newState=DEGRADED" in line
        assert "src=srv-4" in line


class TestJSONLinesExporter:
    def test_minimal_tool_invocation(self) -> None:
        lines: list[str] = []
        exporter = JSONLinesExporter(output_fn=lines.append)

        exporter.export_tool_invocation("srv-1", "echo", "success", 12.5)

        assert exporter.lines_exported == 1
        payload = json.loads(_single_line(lines))
        assert payload["event_type"] == "ToolInvocationCompleted"
        assert payload["provider_id"] == "srv-1"
        assert payload["tool_name"] == "echo"
        assert payload["status"] == "success"
        assert payload["duration_ms"] == 12.5

    def test_full_tool_invocation_with_caller_and_cost(self) -> None:
        lines: list[str] = []
        exporter = JSONLinesExporter(output_fn=lines.append)

        exporter.export_tool_invocation(
            "srv-2",
            "analyze",
            "success",
            42.0,
            user_id="alice",
            session_id="sess-1",
            caller_type="agent",
            caller_id="agent-7",
            caller_roles="admin,ops",
            cost_cents=99,
            cost_model="gpt-4.1",
            cost_input_tokens=100,
            cost_output_tokens=25,
        )

        assert exporter.lines_exported == 1
        payload = json.loads(_single_line(lines))
        assert payload["event_type"] == "ToolInvocationCompleted"
        assert payload["provider_id"] == "srv-2"
        assert payload["tool_name"] == "analyze"
        assert payload["status"] == "success"
        assert payload["duration_ms"] == 42.0
        assert payload["user_id"] == "alice"
        assert payload["session_id"] == "sess-1"

    def test_tool_failed_invocation(self) -> None:
        lines: list[str] = []
        exporter = JSONLinesExporter(output_fn=lines.append)

        exporter.export_tool_invocation("srv-3", "fail", "error", 7.0, error_type="RuntimeError")

        assert exporter.lines_exported == 1
        payload = json.loads(_single_line(lines))
        assert payload["event_type"] == "ToolInvocationFailed"
        assert payload["provider_id"] == "srv-3"
        assert payload["tool_name"] == "fail"
        assert payload["status"] == "error"
        assert payload["error_type"] == "RuntimeError"

    def test_mcp_server_state_change(self) -> None:
        lines: list[str] = []
        exporter = JSONLinesExporter(output_fn=lines.append)

        exporter.export_mcp_server_state_change("srv-4", "READY", "DEGRADED")

        assert exporter.lines_exported == 1
        payload = json.loads(_single_line(lines))
        assert payload["event_type"] == "ProviderStateChanged"
        assert payload["provider_id"] == "srv-4"
        assert payload["from_state"] == "READY"
        assert payload["to_state"] == "DEGRADED"


class TestSyslogExporter:
    def test_minimal_tool_invocation(self) -> None:
        lines: list[str] = []
        exporter = SyslogExporter(output_fn=lines.append)

        exporter.export_tool_invocation("srv-1", "echo", "success", 12.5)

        assert exporter.lines_exported == 1
        line = _single_line(lines)
        assert line.startswith("<")
        assert "mcp-hangar" in line
        assert 'provider="srv-1"' in line
        assert "Tool echo on provider srv-1 success" in line

    def test_full_tool_invocation_with_caller_and_cost(self) -> None:
        lines: list[str] = []
        exporter = SyslogExporter(output_fn=lines.append)

        exporter.export_tool_invocation(
            "srv-2",
            "analyze",
            "success",
            42.0,
            user_id="alice",
            session_id="sess-1",
            caller_type="agent",
            caller_id="agent-7",
            caller_roles="admin,ops",
            cost_cents=99,
            cost_model="gpt-4.1",
            cost_input_tokens=100,
            cost_output_tokens=25,
        )

        assert exporter.lines_exported == 1
        line = _single_line(lines)
        assert 'provider="srv-2"' in line
        assert 'tool="analyze"' in line
        assert 'user="alice"' in line
        assert 'session="sess-1"' in line

    def test_tool_failed_invocation(self) -> None:
        lines: list[str] = []
        exporter = SyslogExporter(output_fn=lines.append)

        exporter.export_tool_invocation("srv-3", "fail", "error", 7.0, error_type="RuntimeError")

        assert exporter.lines_exported == 1
        line = _single_line(lines)
        assert 'provider="srv-3"' in line
        assert 'tool="fail"' in line
        assert 'error="RuntimeError"' in line

    def test_mcp_server_state_change(self) -> None:
        lines: list[str] = []
        exporter = SyslogExporter(output_fn=lines.append)

        exporter.export_mcp_server_state_change("srv-4", "READY", "DEGRADED")

        assert exporter.lines_exported == 1
        line = _single_line(lines)
        assert 'provider="srv-4"' in line
        assert 'fromState="READY"' in line
        assert 'toState="DEGRADED"' in line


def _record(tenant_id: str | None) -> AuditRecord:
    return AuditRecord(
        event_id="evt-1",
        event_type="ToolInvocationCompleted",
        occurred_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        mcp_server_id="srv-1",
        data={"tool_name": "echo", "status": "success", "duration_ms": 12.5},
        caller_user_id="alice",
        caller_session_id="sess-1",
        tenant_id=tenant_id,
    )


# format -> (formatter, the session field, the tenant field "t-1" adds after it)
TENANT_FIELD: dict[str, tuple[Callable[[AuditRecord], str], str, str]] = {
    "cef": (format_audit_record, "cs3Label=SessionID", " cs6=t-1 cs6Label=TenantID"),
    "leef": (_format_leef, "sessID=sess-1", "\ttenantID=t-1"),
    "jsonlines": (_record_to_json_line, '"session_id": "sess-1"', ', "tenant_id": "t-1"'),
    "syslog": (_format_syslog, 'session="sess-1"', ' tenant="t-1"'),
}
TENANT_KEYS = ("cs6", "tenantID", "tenant_id", "tenant=")


class TestTenantInComplianceOutput:
    @pytest.mark.parametrize("fmt", sorted(TENANT_FIELD))
    def test_the_tenant_is_printed_next_to_the_session(self, fmt: str) -> None:
        formatter, session_field, tenant_field = TENANT_FIELD[fmt]

        assert session_field + tenant_field in formatter(_record("t-1"))

    @pytest.mark.parametrize("fmt", sorted(TENANT_FIELD))
    def test_the_tenant_field_is_the_only_difference(self, fmt: str) -> None:
        # Every existing field keeps its text and its order.
        formatter, _, tenant_field = TENANT_FIELD[fmt]

        assert formatter(_record("t-1")).replace(tenant_field, "", 1) == formatter(_record(None))

    @pytest.mark.parametrize("fmt", sorted(TENANT_FIELD))
    @pytest.mark.parametrize("tenant_id", [None, ""], ids=["none", "empty"])
    def test_without_a_tenant_no_tenant_field_is_printed(self, fmt: str, tenant_id: str | None) -> None:
        formatter = TENANT_FIELD[fmt][0]

        line = formatter(_record(tenant_id))

        assert not [key for key in TENANT_KEYS if key in line], line

    def test_cef_escapes_the_tenant_as_an_extension_value(self) -> None:
        line = format_audit_record(_record("a|b=c\\d\ne\rf"))

        assert "cs6=a|b\\=c\\\\d\\ne\\rf cs6Label=TenantID" in line
        assert "\n" not in line and "\r" not in line
        assert line.split("|", 7)[:7] == format_audit_record(_record(None)).split("|", 7)[:7]

    def test_leef_escapes_the_tenant_as_an_attribute_value(self) -> None:
        line = _format_leef(_record("a\tb|c=d\\e\nf\rg"))

        assert "\ttenantID=a\\tb|c=d\\\\e\\nf\\rg\t" in line
        assert "\n" not in line and "\r" not in line
        assert line.count("\t") == _format_leef(_record(None)).count("\t") + 1

    def test_jsonlines_escapes_the_tenant_as_a_json_string(self) -> None:
        tenant = 'a"b\\c\nd]e|f=g\th'
        line = _record_to_json_line(_record(tenant))

        assert "\n" not in line
        assert json.loads(line)["tenant_id"] == tenant

    def test_syslog_escapes_the_tenant_as_a_param_value(self) -> None:
        line = _format_syslog(_record('a"b]c\\d=e'))

        assert ' tenant="a\\"b\\]c\\\\d=e"]' in line
        assert line.endswith("] Tool echo on provider srv-1 success")

    @pytest.mark.parametrize("exporter_class", [CEFExporter, LEEFExporter, JSONLinesExporter, SyslogExporter])
    def test_the_callers_tenant_reaches_every_format(self, exporter_class: Callable[..., Any]) -> None:
        identity = IdentityContext(
            caller=CallerIdentity(user_id="alice", agent_id=None, session_id="sess-1", tenant_id="t-1"),
            correlation_id="corr-1",
        ).to_dict()
        lines: list[str] = []
        handler = OTLPAuditEventHandler(audit_exporter=exporter_class(output_fn=lines.append))

        handler.handle(
            ToolInvocationCompleted(mcp_server_id="srv-1", tool_name="echo", duration_ms=1.0, identity_context=identity)
        )

        line = _single_line(lines)
        assert any(key in line for key in TENANT_KEYS), line
        assert "t-1" in line
