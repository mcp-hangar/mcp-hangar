"""The `domain_event` log line is a summary; the full event is a DEBUG line (#1344).

`LoggingEventHandler` used to write `event.to_dict()` into every line, so at the
default INFO level the structured log carried each event's `identity_context`
and free-text fields -- at WARNING for a failed tool call. Maintainer decision 4
on #1276: summary at INFO and WARNING, the full event only at DEBUG.

These assert on what the production pipeline renders: `setup_logging`'s JSON
renderer writing to stderr, level filtering included. The handler choosing not
to emit a field is only half the claim; the other half is that the line an
operator's shipper reads does not contain it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import structlog

from mcp_hangar.application.event_handlers.logging_handler import LoggingEventHandler
from mcp_hangar.domain.events import (
    DomainEvent,
    HealthCheckFailed,
    McpServerDegraded,
    McpServerIdleDetected,
    McpServerStarted,
    McpServerStateChanged,
    McpServerStopped,
    TaskFailed,
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolInvocationRequested,
    ToolWithdrawnRejected,
)
from mcp_hangar.domain.events.discovery import ProviderDegraded
from mcp_hangar.logging_config import setup_logging

CANARY = "CANARY-7f3a"
IDENTITY = {
    "user_id": f"{CANARY}-user",
    "agent_id": None,
    "session_id": f"{CANARY}-session",
    "principal_type": "user",
    "tenant_id": "tenant-a",
    "correlation_id": "corr-1",
}
#: Keys the pipeline adds to every line, independent of the handler.
PIPELINE_KEYS = {"event", "level", "logger", "timestamp", "service"}

Render = Callable[[str, DomainEvent], tuple[list[dict[str, Any]], str]]


@pytest.fixture
def render(capsys: pytest.CaptureFixture[str]) -> Iterator[Render]:
    """Log one event through `setup_logging(json_format=True)` at a given level."""
    saved_config = structlog.get_config()
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level

    def _render(level: str, event: DomainEvent) -> tuple[list[dict[str, Any]], str]:
        setup_logging(level=level, json_format=True)
        # `setup_logging` caches loggers on first use. Left on, the handler's
        # module logger would stay bound to this pipeline for the rest of the
        # session, and a later `capture_logs` test would silently see nothing.
        structlog.configure(cache_logger_on_first_use=False)
        capsys.readouterr()
        LoggingEventHandler().handle(event)
        err = capsys.readouterr().err
        lines = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
        return [line for line in lines if line["event"].startswith("domain_event")], err

    yield _render
    structlog.configure(**saved_config)
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def _failed_call() -> ToolInvocationFailed:
    return ToolInvocationFailed(
        mcp_server_id="math",
        tool_name="add",
        correlation_id="corr-1",
        duration_ms=12.5,
        error_message=f"{CANARY} upstream said: connection refused to 10.0.0.7",
        error_type="OSError",
        identity_context=dict(IDENTITY),
    )


class TestAtInfo:
    def test_a_failed_call_is_one_warning_line_of_identifiers(self, render: Render) -> None:
        event = _failed_call()

        lines, err = render("INFO", event)

        assert len(lines) == 1, lines
        (line,) = lines
        assert line["event"] == "domain_event"
        assert line["level"] == "warning"
        assert set(line) - PIPELINE_KEYS == {
            "event_type",
            "event_id",
            "mcp_server_id",
            "tool_name",
            "error_type",
            "tenant_id",
            "correlation_id",
        }
        assert line["event_type"] == "ToolInvocationFailed"
        assert line["event_id"] == event.event_id
        assert line["mcp_server_id"] == "math"
        assert line["tool_name"] == "add"
        assert line["error_type"] == "OSError"
        assert line["tenant_id"] == "tenant-a"
        assert line["correlation_id"] == "corr-1"
        for forbidden in ("identity_context", "arguments", "error_message"):
            assert forbidden not in line
        # The message, user id and session id reach no line at all, under any key.
        assert CANARY not in err

    def test_arguments_never_reach_a_summary(self, render: Render) -> None:
        event = ToolInvocationRequested(
            mcp_server_id="math",
            tool_name="add",
            correlation_id="corr-1",
            arguments={"note": CANARY},
            identity_context=dict(IDENTITY),
        )

        lines, err = render("INFO", event)

        # Logged at DEBUG, as before, so nothing at INFO -- and no detail line.
        assert lines == []
        assert CANARY not in err

    def test_the_event_tenant_is_used_when_there_is_no_identity(self, render: Render) -> None:
        event = TaskFailed(
            task_id="t-1",
            tenant_id="tenant-b",
            correlation_id="corr-2",
            error_type="TimeoutError",
            error_message=f"{CANARY} task blew up",
        )

        (line,), err = render("INFO", event)

        assert line["tenant_id"] == "tenant-b"
        assert line["error_type"] == "TimeoutError"
        assert "error_message" not in line and "task_id" not in line
        assert CANARY not in err

    def test_an_absent_field_is_omitted_not_blank(self, render: Render) -> None:
        # `tool` and `mcp_server` are this event's names for the tool and server;
        # a missing tenant (anonymous caller) is left out rather than logged null.
        event = ToolWithdrawnRejected(tenant_id=None, mcp_server="math", tool="add")

        (line,), _ = render("INFO", event)

        assert set(line) - PIPELINE_KEYS == {"event_type", "event_id", "mcp_server_id", "tool_name"}
        assert line["mcp_server_id"] == "math"
        assert line["tool_name"] == "add"


class TestAtDebug:
    def test_the_full_event_is_logged_once(self, render: Render) -> None:
        event = _failed_call()

        lines, _ = render("DEBUG", event)

        summaries = [line for line in lines if line["event"] == "domain_event"]
        details = [line for line in lines if line["event"] == "domain_event_detail"]
        assert len(summaries) == 1 and summaries[0]["level"] == "warning"
        assert len(details) == 1, details
        (detail,) = details
        assert detail["level"] == "debug"
        expected = json.loads(json.dumps(event.to_dict()))
        assert {key: detail[key] for key in expected} == expected
        assert detail["identity_context"] == IDENTITY
        assert CANARY in detail["error_message"]

    def test_a_debug_level_event_gets_one_summary_and_one_detail(self, render: Render) -> None:
        event = ToolInvocationCompleted(mcp_server_id="math", tool_name="add", identity_context=dict(IDENTITY))

        lines, _ = render("DEBUG", event)

        assert [(line["event"], line["level"]) for line in lines] == [
            ("domain_event", "debug"),
            ("domain_event_detail", "debug"),
        ]
        assert "identity_context" not in lines[0]


#: The level each event type was logged at before this change. The handler's
#: mapping is not read to build this table -- that would pass by construction.
LEVEL_BEFORE: list[tuple[DomainEvent, str]] = [
    (McpServerDegraded(mcp_server_id="m", consecutive_failures=3, total_failures=5, reason="x"), "warning"),
    (ProviderDegraded(mcp_server_id="m", consecutive_failures=3, total_failures=5, reason="x"), "warning"),
    (_failed_call(), "warning"),
    (HealthCheckFailed(mcp_server_id="m", consecutive_failures=1, error_message="x"), "warning"),
    (McpServerStarted(mcp_server_id="m", mode="subprocess", tools_count=1, startup_duration_ms=1.0), "info"),
    (McpServerStopped(mcp_server_id="m", reason="idle"), "info"),
    (ToolInvocationRequested(mcp_server_id="m", tool_name="t"), "debug"),
    (ToolInvocationCompleted(mcp_server_id="m", tool_name="t"), "debug"),
    (McpServerStateChanged(mcp_server_id="m", old_state="cold", new_state="ready"), "info"),
    (McpServerIdleDetected(mcp_server_id="m", idle_duration_s=1.0, last_used_at=0.0), "info"),
    (TaskFailed(task_id="t-1"), "info"),
]


@pytest.mark.parametrize(("event", "level"), LEVEL_BEFORE, ids=[type(event).__name__ for event, _ in LEVEL_BEFORE])
def test_each_event_keeps_its_level(render: Render, event: DomainEvent, level: str) -> None:
    lines, _ = render("DEBUG", event)

    summaries = [line for line in lines if line["event"] == "domain_event"]
    assert [line["level"] for line in summaries] == [level]


def test_the_table_covers_every_class_the_mapping_names() -> None:
    from mcp_hangar.application.event_handlers.logging_handler import EVENT_LOG_LEVELS

    covered = {type(event) for event, _ in LEVEL_BEFORE}
    named = {cls for classes, _ in EVENT_LOG_LEVELS for cls in classes}
    assert named <= covered, f"extend LEVEL_BEFORE for {named - covered}"
