"""Length limits on span attributes, audit attributes, log fields and event text (#1343).

Maintainer decision 3 on #1276: span attributes and audit record attributes at
most 256 characters, a structured-log value 2048, a free-text field of a domain
event 4096, each overridable by environment variable, and the standard OTEL_*
variables winning over Hangar's own where they exist.

The span and audit cases run in a fresh interpreter each, with the real SDK and
an in-memory exporter in place of the OTLP one: OpenTelemetry registers a global
provider once per process, so a case that registers one decides every case after
it. The log cases render through `setup_logging`'s real JSON pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs

from mcp_hangar.logging_config import (
    TRUNCATION_MARKER,
    _parse_length_limit,
    env_length_limit,
    get_logger,
    setup_logging,
    truncate_text,
)

LONG = 10_000


def _cut_marker(value: str) -> str:
    """The marker ``value`` ends with, or "" when it ends with none."""
    head, sep, tail = value.rpartition("…[truncated ")
    return sep + tail if head and tail.endswith("]") else ""


# ---------------------------------------------------------------------------
# The shared helper
# ---------------------------------------------------------------------------


class TestTruncateText:
    @pytest.mark.parametrize("value", ["", "short", "x" * 256])
    def test_a_value_within_the_limit_is_returned_as_it_is(self, value: str) -> None:
        assert truncate_text(value, 256) is value

    def test_a_long_value_keeps_its_head_and_ends_with_the_count_of_what_was_cut(self) -> None:
        value = "".join(chr(ord("a") + i % 26) for i in range(LONG))

        out = truncate_text(value, 256)

        assert len(out) <= 256
        marker = _cut_marker(out)
        kept = out[: len(out) - len(marker)]
        assert value.startswith(kept) and len(kept) > 200
        assert marker == TRUNCATION_MARKER.format(LONG - len(kept))

    def test_a_limit_too_short_for_the_marker_is_still_a_hard_bound(self) -> None:
        assert truncate_text("x" * 100, 5) == "xxxxx"


class TestEnvLengthLimit:
    def test_unset_or_empty_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_PROBE_LENGTH_LIMIT", raising=False)
        assert env_length_limit("MCP_PROBE_LENGTH_LIMIT") is None
        monkeypatch.setenv("MCP_PROBE_LENGTH_LIMIT", "  ")
        assert env_length_limit("MCP_PROBE_LENGTH_LIMIT") is None

    def test_a_positive_integer_is_the_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_PROBE_LENGTH_LIMIT", " 300 ")
        assert env_length_limit("MCP_PROBE_LENGTH_LIMIT") == 300

    @pytest.mark.parametrize("raw", ["abc", "0", "-5", "unset", "12.5"])
    def test_anything_else_is_ignored_with_one_warning(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        _parse_length_limit.cache_clear()
        monkeypatch.setenv("MCP_PROBE_LENGTH_LIMIT", raw)

        with capture_logs() as logs:
            assert env_length_limit("MCP_PROBE_LENGTH_LIMIT") is None
            assert env_length_limit("MCP_PROBE_LENGTH_LIMIT") is None

        assert [(e["event"], e["variable"], e["value"]) for e in logs] == [
            ("length_limit_invalid", "MCP_PROBE_LENGTH_LIMIT", raw)
        ]


# ---------------------------------------------------------------------------
# Structured-log fields: the production pipeline, as `setup_logging` builds it
# ---------------------------------------------------------------------------

Render = Callable[..., dict[str, Any]]


@pytest.fixture
def render(capsys: pytest.CaptureFixture[str]) -> Iterator[Render]:
    """Log one line through `setup_logging(json_format=True)` and return it, parsed."""
    saved_config = structlog.get_config()
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level

    def _render(**fields: Any) -> dict[str, Any]:
        setup_logging(level="INFO", json_format=True)
        structlog.configure(cache_logger_on_first_use=False)  # see test_domain_event_log_lines_are_summaries
        capsys.readouterr()
        get_logger("length_probe").info("length_probe", **fields)
        lines = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")]
        return next(line for line in lines if line["event"] == "length_probe")

    yield _render
    structlog.configure(**saved_config)
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


class TestStructuredLogFields:
    def test_a_long_field_is_emitted_at_the_limit_with_the_marker(
        self, render: Render, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MCP_LOG_FIELD_LENGTH_LIMIT", raising=False)

        line = render(field="f" * LONG, nested={"inner": ["n" * LONG]}, short="fits", count=7)

        for value in (line["field"], line["nested"]["inner"][0]):
            assert 2000 < len(value) <= 2048 and _cut_marker(value)
        assert line["short"] == "fits" and line["count"] == 7

    def test_the_environment_overrides_the_limit(self, render: Render, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_LOG_FIELD_LENGTH_LIMIT", "500")

        value = render(field="f" * LONG)["field"]

        assert 450 < len(value) <= 500 and _cut_marker(value)

    def test_a_secret_straddling_the_cut_is_still_redacted(
        self, render: Render, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Truncated first, the head of the token would survive: too short for its pattern to match."""
        monkeypatch.delenv("MCP_LOG_FIELD_LENGTH_LIMIT", raising=False)
        token = "tok3nPLACEHOLDERtok3nPLACEHOLDERtok3n"
        # The token begins before the cut (~2030) and ends after it. The space
        # ends it: the pattern is greedy, and would otherwise redact the tail too.
        value = "y" * 2010 + "Bearer " + token + " " + "z" * 8000

        line = render(field=value)

        assert 2000 < len(line["field"]) <= 2048 and _cut_marker(line["field"])
        assert "tok3n" not in json.dumps(line)
        assert "Bearer [REDACTED]" in line["field"]


# ---------------------------------------------------------------------------
# Span attributes: Hangar's own tracer provider, a real SDK exporter
# ---------------------------------------------------------------------------

_SPAN_PRELUDE = """
import json
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from mcp_hangar.observability import tracing as t

built = []

def factory(**kwargs):
    built.append(InMemorySpanExporter())
    return built[-1]

t.OTLP_AVAILABLE = True
t.OTLPSpanExporter = factory
t.BatchSpanProcessor = SimpleSpanProcessor

def record_one_span():
    with t.trace_span("probe", {"long": "a" * 10000, "short": "fits"}) as span:
        span.record_exception(ValueError("e" * 10000))

def lengths(span):
    return {
        "long": len(span.attributes["long"]),
        "short": span.attributes["short"],
        "exception_message": len(span.events[0].attributes["exception.message"]),
    }
"""


def _run(body: str, prelude: str, **env: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MCP_"))}
    proc = subprocess.run(
        [sys.executable, "-c", prelude + body],
        capture_output=True,
        text=True,
        timeout=60,
        env={**clean, **env},
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc


def _observed(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return json.loads(proc.stdout.strip().splitlines()[-1])


_OWNED_SPAN = """
owned = t.init_tracing(otlp_endpoint="http://collector.invalid:4317")
record_one_span()
print(json.dumps({"owned": owned, **lengths(built[0].get_finished_spans()[0])}))
"""


@pytest.mark.otel_sdk
class TestSpanAttributes:
    @pytest.mark.parametrize(
        ("env", "span_attribute", "event_attribute"),
        [
            pytest.param({}, 256, 256, id="default"),
            pytest.param({"MCP_SPAN_ATTRIBUTE_LENGTH_LIMIT": "300"}, 300, 300, id="hangar-variable"),
            pytest.param(
                {"OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT": "500", "MCP_SPAN_ATTRIBUTE_LENGTH_LIMIT": "300"},
                500,
                500,
                id="otel-global-beats-hangar",
            ),
            pytest.param(
                {"OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT": "1000", "MCP_SPAN_ATTRIBUTE_LENGTH_LIMIT": "300"},
                1000,
                300,
                id="otel-span-beats-hangar-for-span-attributes-only",
            ),
            pytest.param(
                {
                    "OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT": "1000",
                    "OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT": "500",
                    "MCP_SPAN_ATTRIBUTE_LENGTH_LIMIT": "300",
                },
                1000,
                500,
                id="otel-span-beats-otel-global",
            ),
        ],
    )
    def test_a_long_attribute_is_exported_at_the_limit(
        self, env: dict[str, str], span_attribute: int, event_attribute: int
    ) -> None:
        seen = _observed(_run(_OWNED_SPAN, _SPAN_PRELUDE, **env))

        assert seen["owned"] is True
        assert seen["long"] == span_attribute
        # An exception's message is a span event attribute: the SDK's global limit.
        assert seen["exception_message"] == event_attribute
        assert seen["short"] == "fits"

    def test_a_provider_registered_first_is_not_reconfigured(self) -> None:
        seen = _observed(
            _run(
                """
host_spans = InMemorySpanExporter()
host = TracerProvider()
host.add_span_processor(SimpleSpanProcessor(host_spans))
trace.set_tracer_provider(host)
owned = t.init_tracing(otlp_endpoint="http://collector.invalid:4317")
record_one_span()
print(json.dumps({"owned": owned, "built": len(built), **lengths(host_spans.get_finished_spans()[0])}))
""",
                _SPAN_PRELUDE,
            )
        )

        assert seen["owned"] is False and seen["built"] == 0
        assert seen["long"] == LONG and seen["exception_message"] == LONG


# ---------------------------------------------------------------------------
# Audit record attributes: Hangar's own logger provider
# ---------------------------------------------------------------------------

_AUDIT_PRELUDE = """
import json
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
try:
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter as InMemory
except ImportError:  # the name before 1.39
    from opentelemetry.sdk._logs.export import InMemoryLogExporter as InMemory
from mcp_hangar.infrastructure.observability import otlp_audit_exporter as m

built = []

def factory(**kwargs):
    built.append(InMemory())
    return built[-1]

m.OTLPLogExporter = factory
m.BatchLogRecordProcessor = SimpleLogRecordProcessor

def export_long():
    m.OTLPAuditExporter().export_tool_invocation("math", "t" * 10000, "success", 1.5, caller_id="alice")

def attributes(exporter):
    return dict(exporter.get_finished_logs()[0].log_record.attributes)
"""

_OWNED_AUDIT = """
owned = m.init_audit_log_export("http://collector.invalid:4317")
export_long()
print(json.dumps({"owned": owned, "attributes": attributes(built[0])}))
"""


@pytest.mark.otel_sdk
class TestAuditRecordAttributes:
    @pytest.mark.parametrize(
        ("env", "limit"),
        [
            pytest.param({}, 256, id="default"),
            pytest.param({"MCP_AUDIT_ATTRIBUTE_LENGTH_LIMIT": "300"}, 300, id="hangar-variable"),
            pytest.param(
                {"OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT": "500", "MCP_AUDIT_ATTRIBUTE_LENGTH_LIMIT": "300"},
                500,
                id="otel-global-beats-hangar",
            ),
            pytest.param(
                {
                    "OTEL_LOGRECORD_ATTRIBUTE_VALUE_LENGTH_LIMIT": "1000",
                    "OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT": "500",
                    "MCP_AUDIT_ATTRIBUTE_LENGTH_LIMIT": "300",
                },
                1000,
                id="otel-logrecord-beats-otel-global",
            ),
        ],
    )
    def test_the_own_provider_bounds_a_long_attribute_with_the_marker(self, env: dict[str, str], limit: int) -> None:
        from mcp_hangar.observability.conventions import MCP, Caller, GenAI, McpServer

        seen = _observed(_run(_OWNED_AUDIT, _AUDIT_PRELUDE, **env))

        assert seen["owned"] is True
        attributes = seen["attributes"]
        tool = attributes[GenAI.TOOL_NAME]
        assert limit - 50 < len(tool) <= limit and _cut_marker(tool)
        assert tool.startswith("t" * (limit - 50))
        # Values under the limit, and numbers, are untouched.
        assert attributes[McpServer.ID] == "math" and attributes[Caller.ID] == "alice"
        assert attributes[MCP.TOOL_DURATION_MS] == 1.5

    def test_a_provider_registered_first_is_left_untouched(self) -> None:
        from mcp_hangar.observability.conventions import GenAI

        seen = _observed(
            _run(
                """
host_logs = InMemory()
host = LoggerProvider()
host.add_log_record_processor(SimpleLogRecordProcessor(host_logs))
set_logger_provider(host)
owned = m.init_audit_log_export("http://collector.invalid:4317")
export_long()
print(json.dumps({"owned": owned, "built": len(built), "attributes": attributes(host_logs)}))
""",
                _AUDIT_PRELUDE,
            )
        )

        assert seen["owned"] is False and seen["built"] == 0
        assert seen["attributes"][GenAI.TOOL_NAME] == "t" * LONG


# ---------------------------------------------------------------------------
# Free-text fields of domain events: bounded once, at construction
# ---------------------------------------------------------------------------


class TestDomainEventText:
    def test_a_long_error_message_is_bounded_where_the_event_is_built(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp_hangar.domain.events import ToolInvocationFailed

        monkeypatch.delenv("MCP_EVENT_TEXT_LENGTH_LIMIT", raising=False)

        event = ToolInvocationFailed(mcp_server_id="math", tool_name="t" * LONG, error_message="e" * LONG)

        assert 4000 < len(event.error_message) <= 4096 and _cut_marker(event.error_message)
        # Identifiers are not free text: only length of prose is bounded here.
        assert event.tool_name == "t" * LONG

    def test_the_environment_overrides_the_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp_hangar.domain.events import HealthCheckFailed

        monkeypatch.setenv("MCP_EVENT_TEXT_LENGTH_LIMIT", "100")

        event = HealthCheckFailed(mcp_server_id="math", error_message="e" * LONG)

        assert 80 < len(event.error_message) <= 100 and _cut_marker(event.error_message)

    def test_short_text_is_unchanged(self) -> None:
        from mcp_hangar.domain.events import McpServerDegraded, TaskFailed

        assert TaskFailed(task_id="t1", error_message="boom").error_message == "boom"
        assert McpServerDegraded("math", 3, 5, reason="x" * 4096).reason == "x" * 4096

    def test_every_string_in_a_list_is_bounded_and_none_is_still_an_empty_list(self) -> None:
        from mcp_hangar.domain.events import EgressPolicyEnforced, EgressPolicyViolationObserved

        for cls in (EgressPolicyViolationObserved, EgressPolicyEnforced):
            reasons = ["r" * LONG, "short"]
            event = cls(mcp_server_id="math", reasons=reasons)
            assert len(event.reasons[0]) <= 4096 and _cut_marker(event.reasons[0])
            assert event.reasons[1] == "short"
        assert EgressPolicyViolationObserved(mcp_server_id="math", reasons=None).reasons == []  # type: ignore[arg-type]

    def test_a_legacy_alias_is_bounded_like_its_parent(self) -> None:
        from mcp_hangar.domain.events.discovery import ProviderDegraded

        assert len(ProviderDegraded("math", 3, 5, reason="x" * LONG).reason) <= 4096

    def test_the_event_store_ws_events_and_the_logging_handler_see_the_bounded_value(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from mcp_hangar.application.event_handlers.logging_handler import LoggingEventHandler
        from mcp_hangar.domain.contracts.event_bus import HandlerKind
        from mcp_hangar.domain.events import ToolInvocationFailed
        from mcp_hangar.infrastructure.event_bus import EventBus
        from mcp_hangar.infrastructure.persistence import InMemoryEventStore
        from mcp_hangar.infrastructure.persistence.event_serializer import EventSerializer
        from mcp_hangar.server.api.serializers import HangarJSONEncoder
        from mcp_hangar.stream_ids import MCP_SERVER, stream_id_for

        store = InMemoryEventStore()
        bus = EventBus(event_store=store)
        ws_payloads: list[str] = []
        # What /ws/events sends for each event (server/api/ws/events.py).
        bus.subscribe_to_all(
            lambda e: ws_payloads.append(json.dumps(e.to_dict(), cls=HangarJSONEncoder)), kind=HandlerKind.PROJECTION
        )
        bus.subscribe_to_all(LoggingEventHandler().handle, kind=HandlerKind.EFFECT)
        caplog.set_level(logging.DEBUG, logger="mcp_hangar.application.event_handlers.logging_handler")

        event = ToolInvocationFailed(mcp_server_id="math", tool_name="add", error_message="e" * LONG)
        bounded = event.error_message
        assert len(bounded) <= 4096

        with capture_logs() as logs:
            bus.publish_aggregate_events(MCP_SERVER, "math", [event])

        assert store.read_stream(stream_id_for(MCP_SERVER, "math"))[0].error_message == bounded
        serializer = EventSerializer()
        assert serializer.deserialize(*serializer.serialize(event)).error_message == bounded  # type: ignore[attr-defined]
        assert json.loads(ws_payloads[0])["error_message"] == bounded
        detail = next(e for e in logs if e["event"] == "domain_event_detail")
        assert detail["error_message"] == bounded
