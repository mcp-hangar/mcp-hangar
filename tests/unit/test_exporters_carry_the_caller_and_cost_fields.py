"""All four compliance formats carry the caller and cost fields a call has (#1705).

`export_tool_invocation` accepted `caller_type`, `caller_id` and the `cost_*`
fields and put them in the record's data, and every format then left them out
of the line. A zero cost is a value and is kept; a field the call lacks is not
invented.
"""

from __future__ import annotations

import json

import pytest

from mcp_hangar.compliance import CEFExporter, JSONLinesExporter, LEEFExporter, SyslogExporter

CALLER = {"caller_type": "agent", "caller_id": "agent-7"}
COST = {"cost_cents": 99, "cost_model": "token", "cost_input_tokens": 100, "cost_output_tokens": 25}


def _line(exporter_cls, **fields) -> str:
    lines: list[str] = []
    exporter_cls(output_fn=lines.append).export_tool_invocation("srv", "echo", "success", 1.0, **fields)
    (line,) = lines
    return line


def test_json_lines_carry_every_field():
    record = json.loads(_line(JSONLinesExporter, **CALLER, **COST))
    assert {key: record[key] for key in (*CALLER, *COST)} == {**CALLER, **COST}


def test_leef_carries_every_field():
    line = _line(LEEFExporter, **CALLER, **COST)
    for fragment in (
        "callerType=agent",
        "callerId=agent-7",
        "costCents=99",
        "costModel=token",
        "costInputTokens=100",
        "costOutputTokens=25",
    ):
        assert f"\t{fragment}" in line or line.endswith(fragment), (fragment, line)


def test_syslog_carries_every_field():
    line = _line(SyslogExporter, **CALLER, **COST)
    for fragment in (
        'callerType="agent"',
        'callerId="agent-7"',
        'costCents="99"',
        'costModel="token"',
        'costInputTokens="100"',
        'costOutputTokens="25"',
    ):
        assert fragment in line, (fragment, line)


def test_cef_carries_the_fields_its_dictionary_has_a_slot_for():
    line = _line(CEFExporter, **CALLER, **COST)
    for fragment in (
        "suid=agent-7",
        "flexString2=agent flexString2Label=CallerType",
        "cn2=99 cn2Label=CostCents",
        "cn3=100 cn3Label=CostInputTokens",
        "flexNumber1=25 flexNumber1Label=CostOutputTokens",
    ):
        assert fragment in line, (fragment, line)
    # Every labelled string slot is taken, so the pricing model is not in CEF.
    assert "token" not in line


def test_a_zero_cost_is_kept_in_every_format():
    zero = {"cost_cents": 0, "cost_input_tokens": 0, "cost_output_tokens": 0}
    record = json.loads(_line(JSONLinesExporter, **zero))
    assert (record["cost_cents"], record["cost_input_tokens"], record["cost_output_tokens"]) == (0, 0, 0)
    leef = _line(LEEFExporter, **zero)
    assert "costCents=0" in leef and "costInputTokens=0" in leef and "costOutputTokens=0" in leef
    syslog = _line(SyslogExporter, **zero)
    assert 'costCents="0"' in syslog and 'costInputTokens="0"' in syslog and 'costOutputTokens="0"' in syslog
    cef = _line(CEFExporter, **zero)
    assert "cn2=0 " in cef and "cn3=0 " in cef and "flexNumber1=0 " in cef


@pytest.mark.parametrize("exporter_cls", [JSONLinesExporter, LEEFExporter, SyslogExporter, CEFExporter])
def test_a_call_without_caller_or_cost_gets_none_invented(exporter_cls):
    line = _line(exporter_cls)
    for name in ("caller_type", "callerType", "costCents", "cost_cents", "cn2", "suid", "flexString2"):
        assert name not in line, (name, line)


@pytest.mark.parametrize(
    ("exporter_cls", "marker"),
    [
        (JSONLinesExporter, '"error_type": '),
        (LEEFExporter, "reason="),
        (SyslogExporter, 'error="'),
        (CEFExporter, "reason="),
    ],
)
@pytest.mark.parametrize("error_type", ["TimeoutError", "-32601", "tool_error", "other"])
def test_error_type_is_written_as_the_event_carries_it(exporter_cls, marker, error_type):
    """The contract: `error_type` is the bounded classifier, not mapped (refusal.ERROR_TYPE_CONTRACT)."""
    lines: list[str] = []
    exporter_cls(output_fn=lines.append).export_tool_invocation("srv", "echo", "error", 1.0, error_type=error_type)
    (line,) = lines
    if exporter_cls is JSONLinesExporter:
        assert json.loads(line)["error_type"] == error_type
    else:
        assert f"{marker}{error_type}" in line, line
