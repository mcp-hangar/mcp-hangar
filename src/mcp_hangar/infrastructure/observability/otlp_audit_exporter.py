"""OTLP audit exporter for security-relevant domain events.

Exports tool invocations and mcp_server state transitions as OTLP log records.
The bootstrap owns the pipeline they travel through (`init_audit_log_export`);
without one -- no SDK, or nothing registered -- records go to the structured log.

MIT licensed -- part of core observability infrastructure.
"""

import threading
import time
from typing import Any

from ...logging_config import get_logger
from ...metrics import record_otlp_audit_export_failure
from ...observability.conventions import MCP, Caller, Cost, GenAI, McpServer

logger = get_logger(__name__)

AUDIT_LOGGER_NAME = "mcp_hangar.audit"

# Upper bound on shutdown_audit_log_export(), for the reason tracing has its own:
# the SDK's shutdown waits out an export in flight to an unreachable collector.
AUDIT_LOG_SHUTDOWN_TIMEOUT_S = 5.0

# The logs API and SDK are underscore modules in every supported release; these
# are the names relied on. ProxyLoggerProvider is what the API hands out until a
# provider is registered, as ProxyTracerProvider is for traces.
try:
    from opentelemetry._logs import LogRecord, SeverityNumber, get_logger_provider, set_logger_provider
    from opentelemetry._logs._internal import ProxyLoggerProvider
    import opentelemetry.sdk._logs as _sdk_logs
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.version import __version__ as _sdk_version

    OTEL_LOGS_AVAILABLE = True
except ImportError:
    OTEL_LOGS_AVAILABLE = False

try:
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter

    OTLP_LOGS_AVAILABLE = True
except ImportError:
    OTLP_LOGS_AVAILABLE = False
    OTLPLogExporter = None

# The record an SDK provider can export. Before 1.38 its Logger passes an API
# LogRecord on without the provider's resource, and the OTLP encoder then fails
# on the batch thread, losing the batch; the SDK's own LogRecord carries it. From
# 1.38 the SDK converts API records itself and deprecates its own, gone in 1.39.
_SdkLogRecord: Any = None
if OTEL_LOGS_AVAILABLE and tuple(int(part) for part in _sdk_version.split(".")[:2]) < (1, 38):
    _SdkLogRecord = _sdk_logs.LogRecord

# Global state, separate from the tracer provider's: each signal has its own.
_audit_provider: Any = None  # Hangar's own SDK LoggerProvider, once registered
_configured = False  # an OTLP endpoint was configured, so audit export is on
_shut_down = False  # Hangar shut its own provider down; it stays the global


class _MeteredLogExporter:
    """Make audit export failures observable, as ``_MeteredSpanExporter`` does for spans.

    ``BatchLogRecordProcessor`` exports on a background thread and swallows
    failures, so an unreachable collector would drop every batch of audit
    records without a signal. This increments
    ``mcp_hangar_otlp_audit_export_failures_total`` when the wrapped exporter
    returns a failure or raises, and changes nothing else: the result or the
    exception is passed on, so the SDK's retry behaviour is untouched.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def export(self, batch: Any) -> Any:
        try:
            result = self._inner.export(batch)
        except Exception:
            record_otlp_audit_export_failure()
            raise
        # By name: the result enum was renamed (LogExportResult -> LogRecordExportResult).
        if getattr(result, "name", None) != "SUCCESS":
            record_otlp_audit_export_failure()
        return result

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return bool(self._inner.force_flush(timeout_millis))


def init_audit_log_export(otlp_endpoint: str | None, service_name: str = "mcp-hangar") -> bool:
    """Set up the pipeline audit records are exported through.

    No endpoint, no audit export. With one, audit export is on
    (``audit_log_export_configured``) whatever happens next, and Hangar
    registers its own LoggerProvider -- a batch processor and a metered OTLP log
    exporter -- unless the SDK is absent (records then go to the structured log)
    or another provider was registered first (records then go through that one,
    which Hangar never replaces and never shuts down).

    Returns:
        True if Hangar's own provider is the registered one.
    """
    global _audit_provider, _configured

    if not otlp_endpoint:
        return False
    _configured = True

    if _audit_provider is not None:
        return True
    if _shut_down:
        logger.warning("audit_log_export_init_refused", reason="already_shut_down")
        return False
    if not (OTEL_LOGS_AVAILABLE and OTLP_LOGS_AVAILABLE):
        logger.info("audit_log_export_sdk_not_installed", fallback="structlog")
        return False
    if _logger_provider_registered():
        logger.info("audit_log_external_provider_in_use", provider=type(get_logger_provider()).__name__)
        return False

    try:
        provider = LoggerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
        exporter = _MeteredLogExporter(OTLPLogExporter(endpoint=otlp_endpoint, insecure=True))
        provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
        # One-shot, and a second registration is refused with only a warning:
        # confirm this one took, as tracing does.
        set_logger_provider(provider)
        if get_logger_provider() is not provider:
            provider.shutdown()
            logger.warning("audit_log_export_init_refused", reason="provider_registered_concurrently")
            return False
    except Exception as e:  # noqa: BLE001 -- fault-barrier: audit export setup must not crash startup
        logger.warning("audit_log_export_initialization_failed", error=str(e))
        return False

    _audit_provider = provider
    logger.info("audit_log_export_initialized", otlp_endpoint=otlp_endpoint)
    return True


def audit_log_export_configured() -> bool:
    """Whether the bootstrap turned audit export on, so `OTLPAuditExporter` is the exporter."""
    return _configured


def shutdown_audit_log_export() -> None:
    """Shut down Hangar's own logger provider, flushing its pending records.

    Only its own: a provider someone else registered, and its processors, are
    left to their owner. Safe to call twice. Returns within
    ``AUDIT_LOG_SHUTDOWN_TIMEOUT_S``; a flush still waiting on an unreachable
    collector is abandoned on its daemon thread.
    """
    global _audit_provider, _shut_down

    provider = _audit_provider
    if provider is None:
        return
    _audit_provider = None
    _shut_down = True

    errors: list[Exception] = []

    def _shutdown() -> None:
        try:
            provider.shutdown()
        except Exception as e:  # noqa: BLE001 -- fault-barrier: audit export shutdown must not crash application
            errors.append(e)

    worker = threading.Thread(target=_shutdown, name="hangar-audit-log-shutdown", daemon=True)
    worker.start()
    worker.join(AUDIT_LOG_SHUTDOWN_TIMEOUT_S)
    if worker.is_alive():
        logger.warning("audit_log_export_shutdown_timed_out", timeout_s=AUDIT_LOG_SHUTDOWN_TIMEOUT_S)
    elif errors:
        logger.warning("audit_log_export_shutdown_error", error=str(errors[0]))
    else:
        logger.info("audit_log_export_shutdown_complete")


def _logger_provider_registered() -> bool:
    """Whether any provider, Hangar's or another's, owns the OTel logs global.

    Until one is registered the API returns its ``ProxyLoggerProvider``, which
    drops records. ``OTEL_PYTHON_LOGGER_PROVIDER``, when set, is loaded and
    registered by this first lookup.
    """
    return not isinstance(get_logger_provider(), ProxyLoggerProvider)


class OTLPAuditExporter:
    """Exports security-relevant events as OTLP log records.

    Each tool invocation and mcp_server state change is exported with
    MCP governance attributes (mcp.server.id, mcp.tool.name, etc.)
    so OTEL-compatible backends can filter and alert on them.

    Export failures are logged at WARNING level and never propagated
    to callers -- observability must not affect correctness.
    """

    def _emit_log_record(self, attributes: dict) -> None:
        """Emit a structured log record with the given attributes.

        This method is the actual OTLP emission point. It is extracted
        to a separate method to allow unit testing via mock patching.

        The record takes the current span's trace context, when there is one.

        Args:
            attributes: Dict of MCP governance attributes to include.
        """
        # Nowhere to hand the record to: no SDK, nothing registered (the API's
        # proxy would drop it), or Hangar's own provider already shut down.
        if not OTEL_LOGS_AVAILABLE or _shut_down or not _logger_provider_registered():
            logger.info("audit_event", **attributes)
            return

        provider = get_logger_provider()
        fields: dict[str, Any] = {
            "timestamp": time.time_ns(),
            "severity_number": SeverityNumber.INFO,
            "severity_text": "INFO",
            "body": attributes.get("mcp.event.name", "mcp.audit"),
            "attributes": attributes,
        }
        if _SdkLogRecord is not None and isinstance(provider, LoggerProvider):
            record = _SdkLogRecord(resource=provider.resource, **fields)
        else:
            record = LogRecord(**fields)
        provider.get_logger(AUDIT_LOGGER_NAME).emit(record)

    def export_tool_invocation(
        self,
        mcp_server_id: str,
        tool_name: str,
        status: str,
        duration_ms: float,
        user_id: str | None = None,
        session_id: str | None = None,
        error_type: str | None = None,
        caller_type: str | None = None,
        caller_id: str | None = None,
        caller_roles: str | None = None,
        cost_cents: int | None = None,
        cost_model: str | None = None,
        cost_input_tokens: int | None = None,
        cost_output_tokens: int | None = None,
    ) -> None:
        """Export a tool invocation event as an audit log record.

        Args:
            mcp_server_id: McpServer that handled the tool call.
            tool_name: Tool that was invoked.
            status: Outcome -- "success", "error", "timeout", "blocked".
            duration_ms: Call duration in milliseconds.
            user_id: Optional calling user identity.
            session_id: Optional MCP session identifier.
            error_type: Optional exception class name on error.
            caller_type: Optional caller type for identity attribution.
            caller_id: Optional caller identifier.
            caller_roles: Optional comma-separated roles.
            cost_cents: Optional cost in hundredths of a cent.
            cost_model: Optional pricing model used.
            cost_input_tokens: Optional input tokens consumed.
            cost_output_tokens: Optional output tokens produced.
        """
        try:
            attributes: dict = {
                "mcp.event.name": "tool_invocation",
                McpServer.ID: mcp_server_id,
                GenAI.TOOL_NAME: tool_name,
                MCP.TOOL_STATUS: status,
                MCP.TOOL_DURATION_MS: duration_ms,
            }
            if user_id is not None:
                attributes[MCP.USER_ID] = user_id
            if session_id is not None:
                attributes[MCP.SESSION_ID] = session_id
            if error_type is not None:
                attributes["mcp.error.type"] = error_type
            if caller_type is not None:
                attributes[Caller.TYPE] = caller_type
            if caller_id is not None:
                attributes[Caller.ID] = caller_id
            if caller_roles is not None:
                attributes[Caller.ROLES] = caller_roles
            if cost_cents is not None:
                attributes[Cost.CENTS] = cost_cents
            if cost_model is not None:
                attributes[Cost.MODEL] = cost_model
            if cost_input_tokens is not None:
                attributes[GenAI.USAGE_INPUT_TOKENS] = cost_input_tokens
            if cost_output_tokens is not None:
                attributes[GenAI.USAGE_OUTPUT_TOKENS] = cost_output_tokens

            self._emit_log_record(attributes)

        except Exception as e:  # noqa: BLE001 -- fault-barrier: export failures must not crash event handlers
            logger.warning(
                "otlp_audit_export_failed",
                audit_event="tool_invocation",
                mcp_server_id=mcp_server_id,
                tool_name=tool_name,
                error=str(e),
            )

    def export_mcp_server_state_change(
        self,
        mcp_server_id: str,
        from_state: str,
        to_state: str,
    ) -> None:
        """Export a mcp_server state transition as an audit log record.

        Args:
            mcp_server_id: McpServer that transitioned.
            from_state: Previous state.
            to_state: New state.
        """
        try:
            attributes: dict = {
                "mcp.event.name": "mcp_server_state_change",
                McpServer.ID: mcp_server_id,
                McpServer.STATE: to_state,
                "mcp.server.previous_state": from_state,
            }
            self._emit_log_record(attributes)

        except Exception as e:  # noqa: BLE001 -- fault-barrier: export failures must not crash event handlers
            logger.warning(
                "otlp_audit_export_failed",
                audit_event="mcp_server_state_change",
                mcp_server_id=mcp_server_id,
                error=str(e),
            )
