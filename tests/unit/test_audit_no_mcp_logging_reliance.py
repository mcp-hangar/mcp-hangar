"""Regression guard: audit/compliance emission is OTEL/structured-only.

SEP-2577 deprecates the MCP protocol ``logging`` capability (``logging/setLevel``
and ``notifications/message``). That capability is client-controlled and is the
wrong transport for governance/audit evidence. Hangar's audit path is instead
event-sourced and emitted via the OTEL logs bridge (with a structlog fallback)
and SIEM compliance exporters (CEF/LEEF/JSON-lines/syslog).

This module complements ``test_no_mcp_logging_dependency.py`` (a source-string
scan) with *behavioral* assertions that drive the real audit pipeline and prove
the emission point is the OTEL/structured exporter -- never an MCP logging send
path.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from mcp_hangar.application.event_handlers.audit_event_handler import (
    OTLPAuditEventHandler,
)
from mcp_hangar.compliance import JSONLinesExporter
from mcp_hangar.domain.events import ToolInvocationCompleted
from mcp_hangar.infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter

# MCP-protocol logging send-path identifiers that the audit path must NOT touch.
_FORBIDDEN_MCP_LOGGING_API = (
    "send_log_message",
    "set_logging_level",
    "set_level",
    "notification",
    "notify_log",
    "logging_message",
)

# MCP-protocol logging wire strings that must be absent from the audit modules.
_FORBIDDEN_MCP_LOGGING_STRINGS = (
    "logging/setLevel",
    "notifications/message",
    "LoggingCapability",
)


def test_full_audit_pipeline_emits_via_otel_structured_exporter() -> None:
    """A domain event flowing through the audit pipeline hits the OTEL emit point.

    The event bus handler forwards to ``OTLPAuditExporter``, whose sole emission
    point is ``_emit_log_record`` (OTEL logs bridge / structlog fallback). We
    assert that point is reached with OTEL-namespaced (``mcp.*``) attributes --
    i.e. audit evidence is produced by the OTEL/structured exporter, not by any
    MCP logging notification.
    """
    exporter = OTLPAuditExporter()
    handler = OTLPAuditEventHandler(audit_exporter=exporter)

    event = ToolInvocationCompleted(
        mcp_server_id="math",
        tool_name="add",
        correlation_id="corr-1",
        duration_ms=10.5,
        result_size_bytes=128,
    )

    with patch.object(exporter, "_emit_log_record") as mock_emit:
        handler.handle(event)

    mock_emit.assert_called_once()
    attributes = mock_emit.call_args[0][0]
    # OTEL semantic-convention attributes prove the structured exporter emitted.
    assert attributes.get("mcp.server.id") == "math"
    assert attributes.get("gen_ai.tool.name") == "add"
    assert any(key.startswith("mcp.") for key in attributes)


def test_audit_pipeline_never_invokes_an_mcp_logging_send_path() -> None:
    """No MCP logging send API is called while producing audit evidence.

    We hand the handler an exporter that raises if any MCP-protocol logging
    method is accessed, then drive a real event through it. The audit path must
    complete using only its structured ``export_*`` API.
    """

    class TrippingExporter:
        """Real IAuditExporter surface; explodes on any MCP logging attribute."""

        def __init__(self) -> None:
            self.exported = False

        def export_tool_invocation(self, *args: object, **kwargs: object) -> None:
            self.exported = True

        def export_mcp_server_state_change(self, *args: object, **kwargs: object) -> None:
            self.exported = True

        def __getattr__(self, name: str) -> object:
            if name in _FORBIDDEN_MCP_LOGGING_API:
                raise AssertionError(
                    f"audit path reached MCP logging send API '{name}' -- audit must be OTEL/structured only (SEP-2577)"
                )
            raise AttributeError(name)

    exporter = TrippingExporter()
    handler = OTLPAuditEventHandler(audit_exporter=exporter)
    handler.handle(
        ToolInvocationCompleted(
            mcp_server_id="math",
            tool_name="add",
            correlation_id="corr-2",
            duration_ms=1.0,
            result_size_bytes=0,
        )
    )
    assert exporter.exported is True


def test_audit_and_compliance_exporters_expose_no_mcp_logging_send_api() -> None:
    """Audit/compliance exporters and the handler expose no MCP logging methods."""
    surfaces: list[object] = [
        OTLPAuditExporter(),
        JSONLinesExporter(output_fn=lambda _line: None),
        OTLPAuditEventHandler(audit_exporter=MagicMock()),
    ]
    for surface in surfaces:
        for forbidden in _FORBIDDEN_MCP_LOGGING_API:
            assert not hasattr(surface, forbidden), (
                f"{type(surface).__name__} exposes MCP logging send API '{forbidden}'"
            )


def test_compliance_exporter_emits_to_structured_sink_not_mcp() -> None:
    """Compliance export writes a JSON-lines record to its configured SIEM sink.

    The sink is a plain callable (file/syslog/collector in production), fully
    decoupled from any MCP session -- confirming compliance evidence does not
    ride the MCP logging channel.
    """
    lines: list[str] = []
    exporter = JSONLinesExporter(output_fn=lines.append)

    exporter.export_tool_invocation("srv-1", "echo", "success", 12.5, user_id="alice")

    assert exporter.lines_exported == 1
    assert len(lines) == 1
    assert '"tool_name": "echo"' in lines[0]


def test_audit_modules_contain_no_mcp_logging_wire_strings() -> None:
    """The audit/compliance emission modules carry no MCP logging wire strings."""
    src_root = Path(__file__).resolve().parents[2] / "src" / "mcp_hangar"
    audit_modules = [
        src_root / "infrastructure" / "observability" / "otlp_audit_exporter.py",
        src_root / "application" / "event_handlers" / "audit_event_handler.py",
        src_root / "application" / "event_handlers" / "audit_handler.py",
        *sorted((src_root / "compliance").glob("*.py")),
    ]
    for module in audit_modules:
        content = module.read_text(encoding="utf-8")
        for forbidden in _FORBIDDEN_MCP_LOGGING_STRINGS:
            assert forbidden not in content, (
                f"MCP logging wire string '{forbidden}' present in {module.relative_to(src_root.parent.parent)}"
            )
