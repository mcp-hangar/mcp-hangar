"""Unit tests for compliance export formats."""

from collections.abc import Callable
import json
import re
from typing import Protocol, cast

import pytest

from mcp_hangar.compliance.jsonlines_exporter import JSONLinesExporter
from mcp_hangar.compliance.leef_exporter import LEEFExporter
from mcp_hangar.compliance.syslog_exporter import SyslogExporter


class SupportsExport(Protocol):
    @property
    def lines_exported(self) -> int: ...

    def export_tool_invocation(
        self,
        provider_id: str,
        tool_name: str,
        status: str,
        duration_ms: float,
        user_id: str | None = None,
        session_id: str | None = None,
        error_type: str | None = None,
    ) -> None: ...

    def export_provider_state_change(self, provider_id: str, from_state: str, to_state: str) -> None: ...


ExporterFactory = Callable[[Callable[[str], None]], SupportsExport]


JSONScalar = str | int | float | bool | None
JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


def _parse_json_object(line: str) -> dict[str, JSONValue]:
    payload_raw = cast(object, json.loads(line))
    if not isinstance(payload_raw, dict):
        raise AssertionError("Expected JSON object")
    return _coerce_json_dict(cast(dict[object, object], payload_raw))


def _coerce_json_dict(value: object) -> dict[str, JSONValue]:
    if not isinstance(value, dict):
        raise AssertionError("Expected dict")
    dict_value = cast(dict[object, object], value)
    return {str(key): _coerce_json_value(item) for key, item in dict_value.items()}


def _coerce_json_list(value: object) -> list[JSONValue]:
    if not isinstance(value, list):
        raise AssertionError("Expected list")
    list_value = cast(list[object], value)
    return [_coerce_json_value(item) for item in list_value]


def _coerce_json_value(value: object) -> JSONValue:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return _coerce_json_dict(cast(dict[object, object], value))
    if isinstance(value, list):
        return _coerce_json_list(cast(list[object], value))
    raise AssertionError(f"Unexpected JSON value type: {type(value)!r}")


def _build_jsonlines_exporter(output_fn: Callable[[str], None]) -> SupportsExport:
    return JSONLinesExporter(output_fn=output_fn)


def _build_leef_exporter(output_fn: Callable[[str], None]) -> SupportsExport:
    return LEEFExporter(output_fn=output_fn)


def _build_syslog_exporter(output_fn: Callable[[str], None]) -> SupportsExport:
    return SyslogExporter(output_fn=output_fn)


@pytest.mark.parametrize(
    "exporter_factory",
    [
        _build_jsonlines_exporter,
        _build_leef_exporter,
        _build_syslog_exporter,
    ],
)
def test_output_fn_receives_formatted_lines_and_counter_increments(exporter_factory: ExporterFactory) -> None:
    lines: list[str] = []
    exporter = exporter_factory(lines.append)

    exporter.export_tool_invocation("math", "add", "success", 12.5, user_id="alice", session_id="sess-1")
    exporter.export_provider_state_change("math", "READY", "DEGRADED")

    assert len(lines) == 2
    assert all(isinstance(line, str) and line for line in lines)
    assert exporter.lines_exported == 2


def test_jsonlines_tool_invocation_produces_valid_json() -> None:
    lines: list[str] = []
    exporter = JSONLinesExporter(output_fn=lines.append)

    exporter.export_tool_invocation(
        "math",
        "add",
        "success",
        12.5,
        user_id="alice",
        session_id="sess-1",
    )

    payload = _parse_json_object(lines[0])
    assert payload["event_type"] == "ToolInvocationCompleted"
    assert payload["provider_id"] == "math"
    assert payload["tool_name"] == "add"
    assert payload["status"] == "success"
    assert payload["duration_ms"] == 12.5
    assert payload["user_id"] == "alice"
    assert payload["session_id"] == "sess-1"
    assert "error_type" not in payload
    timestamp = payload["timestamp"]
    assert isinstance(timestamp, str)
    assert timestamp.endswith("+00:00")


def test_jsonlines_provider_state_change_omits_null_values() -> None:
    lines: list[str] = []
    exporter = JSONLinesExporter(output_fn=lines.append)

    exporter.export_provider_state_change("math", "READY", "DEGRADED")

    payload = _parse_json_object(lines[0])
    assert payload == {
        "timestamp": payload["timestamp"],
        "event_type": "ProviderStateChanged",
        "provider_id": "math",
        "from_state": "READY",
        "to_state": "DEGRADED",
    }


def test_leef_tool_invocation_uses_leef_header_and_extensions() -> None:
    lines: list[str] = []
    exporter = LEEFExporter(output_fn=lines.append)

    exporter.export_tool_invocation(
        "math",
        "add",
        "success",
        12.5,
        user_id="alice",
        session_id="sess-1",
    )

    line = lines[0]
    assert line.startswith("LEEF:2.0|MCP Hangar|MCP Hangar|0.15.0|101|\t")
    assert "\tproto=tool" in line
    assert "\taction=add" in line
    assert "\tduration=12.5" in line
    assert "\tusrName=alice" in line
    assert "\tsessID=sess-1" in line
    assert "\tsrc=math" in line


def test_leef_provider_state_change_uses_state_change_event_id() -> None:
    lines: list[str] = []
    exporter = LEEFExporter(output_fn=lines.append)

    exporter.export_provider_state_change("math", "READY", "DEGRADED")

    line = lines[0]
    assert line.startswith("LEEF:2.0|MCP Hangar|MCP Hangar|0.15.0|202|\t")
    assert "\toldState=READY" in line
    assert "\tnewState=DEGRADED" in line
    assert "\tsrc=math" in line


def test_syslog_tool_invocation_uses_rfc5424_structure() -> None:
    lines: list[str] = []
    exporter = SyslogExporter(output_fn=lines.append)

    exporter.export_tool_invocation(
        "math",
        "add",
        "success",
        12.5,
        user_id="alice",
        session_id="sess-1",
    )

    line = lines[0]
    assert re.match(
        r"^<134>1 \S+ \S+ mcp-hangar \d+ 101 \[mcp@49152[^\]]*\] Tool add on provider math success$",
        line,
    )
    assert 'provider="math"' in line
    assert 'tool="add"' in line
    assert 'status="success"' in line
    assert 'duration="12.5"' in line
    assert 'user="alice"' in line
    assert 'session="sess-1"' in line


def test_syslog_provider_state_change_uses_warning_priority() -> None:
    lines: list[str] = []
    exporter = SyslogExporter(output_fn=lines.append)

    exporter.export_provider_state_change("math", "READY", "DEGRADED")

    line = lines[0]
    assert re.match(
        r"^<132>1 \S+ \S+ mcp-hangar \d+ 202 \[mcp@49152[^\]]*\] Provider math state changed from READY to DEGRADED$",
        line,
    )
    assert 'fromState="READY"' in line
    assert 'toState="DEGRADED"' in line
    assert 'provider="math"' in line
