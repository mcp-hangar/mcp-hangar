"""A refused call reaches all four compliance formats as its own event type (#1582).

Each format mapped a status it did not know to ``ToolInvocationRequested``, so a
refusal would have read as a request that had merely been made. ``denied`` is
``ToolInvocationDenied`` now, in CEF, LEEF, JSON lines and syslog alike, with
the refusal's bounded gate or L7 fields and nothing outside that vocabulary.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from mcp_hangar.compliance.cef_exporter import CEFExporter
from mcp_hangar.compliance.jsonlines_exporter import JSONLinesExporter
from mcp_hangar.compliance.leef_exporter import LEEFExporter
from mcp_hangar.compliance.refusal import TOOL_INVOCATION_DENIED, event_type_for_status
from mcp_hangar.compliance.syslog_exporter import SyslogExporter
from mcp_hangar.observability.conventions import L7, Gate

_FORMATS: dict[str, Callable[..., Any]] = {
    "cef": CEFExporter,
    "leef": LEEFExporter,
    "jsonlines": JSONLinesExporter,
    "syslog": SyslogExporter,
}

#: The event type's marker in each format's line: CEF's signature and name, LEEF's
#: event id, the JSON field, syslog's MSGID with its warning priority (local0).
_DENIED_MARKER = {
    "cef": "|103|Tool Invocation Denied|5|",
    "leef": "|103|",
    "jsonlines": f'"event_type": "{TOOL_INVOCATION_DENIED}"',
    "syslog": "<132>1 ",
}

_OUTSIDE = "text-from-outside-the-vocabulary"


def _export(fmt: str, **kwargs: Any) -> str:
    lines: list[str] = []
    exporter = _FORMATS[fmt](output_fn=lines.append)
    exporter.export_tool_invocation(mcp_server_id="server_a", tool_name="read_item", duration_ms=1.5, **kwargs)
    [line] = lines
    return line


def test_denied_is_its_own_event_type() -> None:
    assert event_type_for_status("denied") == TOOL_INVOCATION_DENIED
    assert event_type_for_status("something-else") == "ToolInvocationRequested"


@pytest.mark.parametrize("fmt", sorted(_FORMATS))
def test_a_gate_refusal_carries_the_gate_and_its_reason(fmt: str) -> None:
    line = _export(
        fmt,
        status="denied",
        tenant_id="tenant-a",
        refusal={Gate.NAME: "approval", Gate.REASON: "approval_denied", "free.text": _OUTSIDE},
    )

    assert _DENIED_MARKER[fmt] in line, line
    assert "ToolInvocationRequested" not in line and "|100|" not in line
    assert "approval" in line and "approval_denied" in line
    assert "tenant-a" in line
    assert _OUTSIDE not in line, "a key outside the vocabulary is dropped"


@pytest.mark.parametrize("fmt", sorted(_FORMATS))
def test_an_l7_refusal_carries_the_verdict(fmt: str) -> None:
    refusal = {L7.VERDICT: "require_approval", L7.MODE: "enforce", L7.RULE_KIND: "tool", L7.POLICY_ID: "sha256:ab"}

    line = _export(fmt, status="denied", refusal=refusal)

    assert _DENIED_MARKER[fmt] in line, line
    for value in refusal.values():
        assert value in line, value


def test_json_lines_names_the_fields() -> None:
    line = _export("jsonlines", status="denied", refusal={Gate.NAME: "tool_access", Gate.REASON: "access_denied"})

    payload = json.loads(line)
    assert (payload["status"], payload["gate"], payload["gate_reason"]) == ("denied", "tool_access", "access_denied")


@pytest.mark.parametrize("fmt", sorted(_FORMATS))
def test_other_statuses_are_unchanged(fmt: str) -> None:
    line = _export(fmt, status="success")

    assert "Denied" not in line and "gate" not in line
