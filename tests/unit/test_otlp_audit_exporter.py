"""Unit tests for OTLPAuditExporter and the audit log pipeline it exports through."""

import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest


class TestOTLPAuditExporter:
    """OTLPAuditExporter must emit log records for security-relevant events."""

    def test_export_tool_invocation_success_emits_log_record(self) -> None:
        from mcp_hangar.infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter
        from mcp_hangar.observability.conventions import GenAI, MCP, McpServer

        exporter = OTLPAuditExporter()

        with patch.object(exporter, "_emit_log_record") as mock_emit:
            exporter.export_tool_invocation(
                mcp_server_id="math",
                tool_name="add",
                status="success",
                duration_ms=12.5,
            )
            mock_emit.assert_called_once()
            record = mock_emit.call_args[0][0]
            assert record.get(McpServer.ID) == "math"
            assert record.get(GenAI.TOOL_NAME) == "add"
            assert record.get(MCP.TOOL_STATUS) == "success"

    def test_export_tool_invocation_error_includes_error_type(self) -> None:
        from mcp_hangar.infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter

        exporter = OTLPAuditExporter()

        with patch.object(exporter, "_emit_log_record") as mock_emit:
            exporter.export_tool_invocation(
                mcp_server_id="p",
                tool_name="t",
                status="error",
                duration_ms=5.0,
                error_type="ToolInvocationError",
            )
            record = mock_emit.call_args[0][0]
            assert record.get("mcp.tool.status") == "error"
            assert record.get("mcp.error.type") == "ToolInvocationError"

    def test_export_mcp_server_state_change_emits_log_record(self) -> None:
        from mcp_hangar.infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter
        from mcp_hangar.observability.conventions import McpServer

        exporter = OTLPAuditExporter()

        with patch.object(exporter, "_emit_log_record") as mock_emit:
            exporter.export_mcp_server_state_change("math", "READY", "DEGRADED")
            record = mock_emit.call_args[0][0]
            assert record.get(McpServer.ID) == "math"
            assert record.get(McpServer.STATE) == "DEGRADED"
            assert record.get("mcp.server.previous_state") == "READY"

    def test_export_failure_does_not_raise(self) -> None:
        """Exporter must swallow export errors to avoid crashing event handlers."""
        from mcp_hangar.infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter

        exporter = OTLPAuditExporter()

        with patch.object(exporter, "_emit_log_record", side_effect=RuntimeError("OTLP unavailable")):
            # Must not raise
            exporter.export_tool_invocation("p", "t", "success", 1.0)

    def test_null_exporter_is_no_op(self) -> None:
        from mcp_hangar.application.ports.observability import NullAuditExporter

        exporter = NullAuditExporter()
        exporter.export_tool_invocation("p", "t", "success", 1.0)
        exporter.export_mcp_server_state_change("p", "COLD", "READY")
        # No error, no output

    def test_export_tool_invocation_includes_caller_attributes(self) -> None:
        from mcp_hangar.infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter
        from mcp_hangar.observability.conventions import Caller

        exporter = OTLPAuditExporter()

        with patch.object(exporter, "_emit_log_record") as mock_emit:
            exporter.export_tool_invocation(
                mcp_server_id="math",
                tool_name="add",
                status="success",
                duration_ms=10.0,
                caller_type="human",
                caller_id="alice",
                caller_roles="admin,viewer",
            )
            record = mock_emit.call_args[0][0]
            assert record[Caller.TYPE] == "human"
            assert record[Caller.ID] == "alice"
            assert record[Caller.ROLES] == "admin,viewer"

    def test_export_tool_invocation_includes_cost_attributes(self) -> None:
        from mcp_hangar.infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter
        from mcp_hangar.observability.conventions import Cost, GenAI

        exporter = OTLPAuditExporter()

        with patch.object(exporter, "_emit_log_record") as mock_emit:
            exporter.export_tool_invocation(
                mcp_server_id="llm",
                tool_name="generate",
                status="success",
                duration_ms=500.0,
                cost_cents=250,
                cost_model="token",
                cost_input_tokens=1000,
                cost_output_tokens=500,
            )
            record = mock_emit.call_args[0][0]
            assert record[Cost.CENTS] == 250
            assert record[Cost.MODEL] == "token"
            assert record[GenAI.USAGE_INPUT_TOKENS] == 1000
            assert record[GenAI.USAGE_OUTPUT_TOKENS] == 500

    def test_export_tool_invocation_omits_none_caller_cost(self) -> None:
        from mcp_hangar.infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter
        from mcp_hangar.observability.conventions import Caller, Cost

        exporter = OTLPAuditExporter()

        with patch.object(exporter, "_emit_log_record") as mock_emit:
            exporter.export_tool_invocation(
                mcp_server_id="p",
                tool_name="t",
                status="success",
                duration_ms=1.0,
            )
            record = mock_emit.call_args[0][0]
            assert Caller.TYPE not in record
            assert Cost.CENTS not in record


# The pipeline, with the real SDK and nothing patched on the emit path (#1289).
# OpenTelemetry registers the global logger provider once per process, so each
# case runs in a fresh interpreter, prints one JSON line of observations, and
# the parent asserts on it and on the child's stderr, where Hangar's logs go.

ENDPOINT = "http://collector.invalid:4317"

_PRELUDE = """
import json
from opentelemetry._logs import get_logger_provider, set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
try:
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter as InMemory
except ImportError:  # the name before 1.39
    from opentelemetry.sdk._logs.export import InMemoryLogExporter as InMemory
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF
from mcp_hangar import metrics
from mcp_hangar.infrastructure.observability import otlp_audit_exporter as m

ENDPOINT = "http://collector.invalid:4317"
built = []

def use_in_memory_exporters(inner=None, batch=False):
    # Hangar's OTLP log exporter becomes `inner` (in-memory by default), and
    # unless `batch`, exported synchronously: nothing leaves the process.
    def factory(**kwargs):
        built.append((kwargs, inner or InMemory()))
        return built[-1][1]
    m.OTLPLogExporter = factory
    if not batch:
        m.BatchLogRecordProcessor = SimpleLogRecordProcessor

def records(exporter):
    out = []
    for item in exporter.get_finished_logs():
        r = item.log_record
        out.append({
            "body": r.body,
            "attributes": dict(r.attributes or {}),
            "trace_id": format(r.trace_id or 0, "032x"),
            "span_id": format(r.span_id or 0, "016x"),
            "sampled": bool(r.trace_flags and r.trace_flags.sampled),
        })
    return out

def ids(span):
    ctx = span.get_span_context()
    return [format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")]

def failures():
    name = "mcp_hangar_otlp_audit_export_failures_total"
    lines = [line for line in metrics.get_metrics().splitlines() if line.startswith(name + " ")]
    return float(lines[0].split()[-1]) if lines else 0.0

def emit(**observed):
    print(json.dumps(observed))
"""


def _run(body: str, prelude: str = _PRELUDE, **env: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MCP_TRACING_"))}
    proc = subprocess.run(
        [sys.executable, "-c", prelude + body],
        capture_output=True,
        text=True,
        timeout=60,
        env={**clean, **env},
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc


def _observed(proc: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.otel_sdk
class TestTheAuditLogPipeline:
    def test_with_nothing_registered_a_record_reaches_the_structured_log(self) -> None:
        """The defect: with the SDK installed and no provider, the proxy dropped it and the fallback was skipped."""
        proc = _run("""
m.OTLPAuditExporter().export_tool_invocation("math", "add", "success", 1.5)
emit(provider=type(get_logger_provider()).__name__)
""")

        assert _observed(proc)["provider"] == "ProxyLoggerProvider"
        assert "audit_event" in proc.stderr and "math" in proc.stderr

    def test_an_owned_pipeline_exports_the_record_with_whatever_trace_context_is_current(self) -> None:
        from mcp_hangar.observability.conventions import MCP, Caller, GenAI, McpServer

        proc = _run("""
use_in_memory_exporters()
owned = m.init_audit_log_export(ENDPOINT)
provider = get_logger_provider()
exporter = m.OTLPAuditExporter()
with TracerProvider().get_tracer("case").start_as_current_span("sampled") as span:
    sampled = ids(span)
    exporter.export_tool_invocation("math", "add", "success", 1.5, caller_id="alice")
exporter.export_mcp_server_state_change("math", "cold", "ready")
with TracerProvider(sampler=ALWAYS_OFF).get_tracer("case").start_as_current_span("unsampled") as span:
    unsampled = ids(span)
    exporter.export_tool_invocation("math", "divide", "error", 0.0, error_type="OSError")
emit(
    owned=owned,
    is_hangars=provider is m._audit_provider,
    configured=m.audit_log_export_configured(),
    exporter_kwargs=built[0][0],
    sampled=sampled,
    unsampled=unsampled,
    records=records(built[0][1]),
    resource=dict(provider.resource.attributes),
)
""")
        seen = _observed(proc)

        assert seen["owned"] is True and seen["is_hangars"] is True and seen["configured"] is True
        assert seen["exporter_kwargs"] == {"endpoint": ENDPOINT, "insecure": True}
        assert seen["resource"]["service.name"] == "mcp-hangar"
        in_span, no_span, unsampled = seen["records"]
        assert in_span["body"] == "tool_invocation"
        assert in_span["attributes"] == {
            "mcp.event.name": "tool_invocation",
            McpServer.ID: "math",
            GenAI.TOOL_NAME: "add",
            MCP.TOOL_STATUS: "success",
            MCP.TOOL_DURATION_MS: 1.5,
            Caller.ID: "alice",
        }
        assert [in_span["trace_id"], in_span["span_id"]] == seen["sampled"] and in_span["sampled"] is True
        # No span: the record is still emitted, with no trace context.
        assert no_span["body"] == "mcp_server_state_change"
        assert (no_span["trace_id"], no_span["span_id"]) == ("0" * 32, "0" * 16)
        # Unsampled: still emitted, and still names the (non-recording) span.
        assert unsampled["attributes"]["mcp.error.type"] == "OSError"
        assert [unsampled["trace_id"], unsampled["span_id"]] == seen["unsampled"] and unsampled["sampled"] is False
        assert proc.stderr.count("audit_log_export_initialized") == 1

    def test_a_provider_registered_first_is_used_never_replaced_flushed_or_shut_down(self) -> None:
        """Through the bootstrap, the way the server starts and stops."""
        proc = _run(
            """
use_in_memory_exporters()
host_logs = InMemory()
host = LoggerProvider()
host.add_log_record_processor(SimpleLogRecordProcessor(host_logs))
set_logger_provider(host)
calls = []
for name in ("force_flush", "shutdown"):
    real = getattr(host, name)
    setattr(host, name, lambda *a, _n=name, _r=real, **k: calls.append(_n) or _r(*a, **k))

from mcp_hangar.server.bootstrap.observability import init_observability, shutdown_observability

init_observability({"observability": {"tracing": {"otlp_endpoint": ENDPOINT}}})
m.OTLPAuditExporter().export_tool_invocation("math", "add", "success", 1.0)
shutdown_observability(None)
m.OTLPAuditExporter().export_tool_invocation("math", "after-hangar-shutdown", "success", 1.0)
emit(
    built=len(built),
    configured=m.audit_log_export_configured(),
    still_host=get_logger_provider() is host,
    tools=[r["attributes"]["gen_ai.tool.name"] for r in records(host_logs)],
    calls=calls,
)
""",
            MCP_TRACING_ENABLED="false",
        )
        seen = _observed(proc)

        assert seen["built"] == 0 and seen["configured"] is True and seen["still_host"] is True
        # The host's provider carried Hangar's record, and still did after Hangar shut down.
        assert seen["tools"] == ["add", "after-hangar-shutdown"]
        assert seen["calls"] == []
        assert "audit_log_external_provider_in_use" in proc.stderr
        for claim in (
            "audit_log_export_initialized",
            "audit_log_export_shutdown",
            "Overriding of current LoggerProvider",
        ):
            assert claim not in proc.stderr, claim

    def test_losing_the_registration_race_leaves_no_orphan_provider(self) -> None:
        proc = _run("""
use_in_memory_exporters()
host = LoggerProvider()
register = m.set_logger_provider

def racing_register(provider):
    register(host)
    register(provider)

m.set_logger_provider = racing_register
owned = m.init_audit_log_export(ENDPOINT)
emit(
    owned=owned,
    claimed=m._audit_provider is not None,
    uses_host=get_logger_provider() is host,
    own_exporter_shut_down=built[0][1].export(()).name == "FAILURE",
)
""")
        seen = _observed(proc)

        assert seen == {"owned": False, "claimed": False, "uses_host": True, "own_exporter_shut_down": True}
        assert "provider_registered_concurrently" in proc.stderr
        assert "audit_log_export_initialized" not in proc.stderr

    @pytest.mark.parametrize("how", ["failure_result", "raises"])
    def test_an_export_failure_is_counted_and_the_handler_does_not_raise(self, how: str) -> None:
        """Through the production batch processor and the real event handler."""
        proc = _run(
            f"HOW = {how!r}\n"
            + """
try:
    from opentelemetry.sdk._logs.export import LogRecordExportResult as Result
except ImportError:  # the name before 1.39
    from opentelemetry.sdk._logs.export import LogExportResult as Result
from mcp_hangar.application.event_handlers.audit_event_handler import OTLPAuditEventHandler
from mcp_hangar.domain.events import McpServerStateChanged, ToolInvocationCompleted

class Collector:
    def export(self, batch):
        if HOW == "raises":
            raise ConnectionError("collector down")
        return Result.FAILURE
    def shutdown(self):
        pass
    def force_flush(self, timeout_millis=30000):
        return True

use_in_memory_exporters(inner=Collector(), batch=True)
assert m.init_audit_log_export(ENDPOINT)
before = failures()
handler = OTLPAuditEventHandler(audit_exporter=m.OTLPAuditExporter())
handler.handle(
    ToolInvocationCompleted(
        mcp_server_id="math", tool_name="add", correlation_id="c", duration_ms=1.0, result_size_bytes=1
    )
)
handler.handle(McpServerStateChanged(mcp_server_id="math", old_state="cold", new_state="ready"))
get_logger_provider().force_flush()
emit(before=before, after=failures())
"""
        )
        seen = _observed(proc)

        # One batch of two records, one failure.
        assert (seen["before"], seen["after"]) == (0.0, 1.0)
        assert "otlp_audit_export_failed" not in proc.stderr

    def test_shutdown_is_bounded_against_an_unreachable_collector(self) -> None:
        proc = _run("""
import socket
import time

# Listens, so the kernel completes the TCP handshake, and never answers: every
# export waits out the full OTLP deadline, as against a hung collector.
sink = socket.socket()
sink.bind(("127.0.0.1", 0))
sink.listen()
assert m.init_audit_log_export(f"http://127.0.0.1:{sink.getsockname()[1]}")
for i in range(3):
    m.OTLPAuditExporter().export_tool_invocation("math", f"pending-{i}", "success", 1.0)
start = time.monotonic()
m.shutdown_audit_log_export()
emit(elapsed=time.monotonic() - start, bound=m.AUDIT_LOG_SHUTDOWN_TIMEOUT_S)
""")
        seen = _observed(proc)

        assert seen["bound"] == 5.0
        assert seen["elapsed"] < seen["bound"] + 1.0, seen
        assert "audit_log_export_shutdown_timed_out" in proc.stderr

    def test_init_is_idempotent_refused_after_shutdown_and_records_then_reach_the_log(self) -> None:
        proc = _run("""
use_in_memory_exporters()
first = m.init_audit_log_export(ENDPOINT)
second = m.init_audit_log_export(ENDPOINT)
m.shutdown_audit_log_export()
m.shutdown_audit_log_export()
again = m.init_audit_log_export(ENDPOINT)
m.OTLPAuditExporter().export_tool_invocation("math", "after-shutdown", "success", 1.0)
emit(first=first, second=second, again=again, built=len(built), exported=len(records(built[0][1])))
""")
        seen = _observed(proc)

        assert seen == {"first": True, "second": True, "again": False, "built": 1, "exported": 0}
        assert proc.stderr.count("audit_log_export_initialized") == 1
        assert proc.stderr.count("audit_log_export_shutdown_complete") == 1
        assert "already_shut_down" in proc.stderr
        assert "audit_event" in proc.stderr and "after-shutdown" in proc.stderr


def test_without_the_sdk_audit_export_stays_on_and_records_reach_the_structured_log() -> None:
    """No SDK installed: nothing to own, and nothing dropped."""
    proc = _run(
        """
import json
import sys

sys.modules["opentelemetry.sdk"] = None  # as on an install without the extra
from mcp_hangar.infrastructure.observability import otlp_audit_exporter as m

owned = m.init_audit_log_export("http://collector.invalid:4317")
m.OTLPAuditExporter().export_tool_invocation("math", "add", "success", 1.0)
print(json.dumps({"available": m.OTEL_LOGS_AVAILABLE, "owned": owned, "configured": m.audit_log_export_configured()}))
""",
        prelude="",
    )

    assert _observed(proc) == {"available": False, "owned": False, "configured": True}
    assert "audit_log_export_sdk_not_installed" in proc.stderr
    assert "audit_event" in proc.stderr and "math" in proc.stderr
