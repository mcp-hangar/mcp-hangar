import json

from enterprise.compliance import CEFExporter, JSONLinesExporter, LEEFExporter, SyslogExporter


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
