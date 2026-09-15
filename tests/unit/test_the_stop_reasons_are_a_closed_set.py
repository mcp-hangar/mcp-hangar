"""The `reason` label of the stop counter is a closed set, and the HELP line lists it (#1360).

An operator writes `reason!="idle"`, or `reason="max_retries_exceeded"` for a
give-up, against the list the metric documents. That list only means something
if nothing outside it reaches the label: the REST stop takes its reason from
the request body, so any other value is counted as `manual`.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import Mock
from uuid import uuid4

import pytest

from mcp_hangar import metrics as m
from mcp_hangar.application.commands import StopMcpServerCommand
from mcp_hangar.application.commands.handlers import StopMcpServerHandler
from mcp_hangar.domain.events import DELIBERATE_STOP_REASONS, STOPPED_BY_GIVING_UP


def _sid() -> str:
    return f"svc-{uuid4().hex[:12]}"


def _stops(sid: str) -> dict[str, float]:
    """Every stop-counter sample for `sid`, by reason."""
    return {
        sample.labels["reason"]: float(sample.value)
        for sample in m.PROVIDER_STOPS_TOTAL.collect()
        if sample.labels.get("mcp_server") == sid
    }


@pytest.mark.parametrize("reason", m.MCP_SERVER_STOP_REASONS)
def test_a_listed_reason_is_counted_as_itself(reason):
    sid = _sid()

    m.record_mcp_server_stop(sid, reason)

    assert _stops(sid) == {reason: 1.0}


@pytest.mark.parametrize("reason", ["maintenance window", "", "IDLE", 5, None, ["idle"], {"reason": "idle"}])
def test_any_other_reason_is_counted_as_manual(reason):
    # A list or a dict is what a JSON body can hold; neither may raise here.
    sid = _sid()

    m.record_mcp_server_stop(sid, cast(Any, reason))

    assert _stops(sid) == {"manual": 1.0}


def test_a_rest_stop_with_its_own_reason_is_recorded_as_manual_and_answered_as_given():
    sid = _sid()
    server = Mock(mcp_server_id=sid)
    server.collect_events.return_value = []
    handler = StopMcpServerHandler(Mock(get=Mock(return_value=server)), Mock())

    result = handler.handle(StopMcpServerCommand(mcp_server_id=sid, reason="maintenance window"))

    assert result == {"stopped": sid, "reason": "maintenance window"}
    # Recorded as `manual`, and counted from that stop's event, not here (#1466).
    server.shutdown.assert_called_once_with(reason="manual")
    assert _stops(sid) == {}


def test_the_reasons_hangar_records_are_all_listed():
    # The give-up's stop, the stop command's default, and the aggregate's own.
    assert STOPPED_BY_GIVING_UP in m.MCP_SERVER_STOP_REASONS
    assert StopMcpServerCommand(mcp_server_id="svc").reason in m.MCP_SERVER_STOP_REASONS
    assert {"idle", "shutdown", "failback", "compensation", "detection_enforcement:block"} <= set(
        m.MCP_SERVER_STOP_REASONS
    )
    assert m.MCP_SERVER_STOP_REASON_OTHER in m.MCP_SERVER_STOP_REASONS
    # Every stop is made on purpose or is a give-up; the saga and the alert
    # handler tell them apart by these (#1466).
    assert set(m.MCP_SERVER_STOP_REASONS) == {*DELIBERATE_STOP_REASONS, STOPPED_BY_GIVING_UP}


def test_the_help_line_lists_every_reason():
    [help_line] = [
        line for line in m.get_metrics().splitlines() if line.startswith("# HELP mcp_hangar_mcp_server_stops_total ")
    ]

    listed = help_line.split("by reason: ", 1)[1].split(", ")
    assert listed == list(m.MCP_SERVER_STOP_REASONS)
