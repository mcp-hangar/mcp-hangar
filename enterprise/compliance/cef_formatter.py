"""CEF (Common Event Format) formatter for audit records.

Converts AuditRecord objects to ArcSight CEF strings for SIEM integration.

CEF format:
    CEF:Version|Device Vendor|Device Product|Device Version|Signature ID|Name|Severity|Extension

Reference: ArcSight Common Event Format (CEF) Rev 25.

This module is part of the enterprise compliance layer (BSL 1.1).
See enterprise/LICENSE.BSL for license terms.
"""

from datetime import datetime, timezone

from mcp_hangar.application.event_handlers.audit_handler import AuditRecord


# CEF header constants
CEF_VERSION = "0"
DEVICE_VENDOR = "MCP Hangar"
DEVICE_PRODUCT = "MCP Hangar"
DEVICE_VERSION = "0.15.0"

# Map event types to CEF signature IDs and human-readable names.
# Signature IDs follow a numbering scheme:
#   1xx = tool invocation events
#   2xx = provider lifecycle events
#   3xx = health check events
#   4xx = security events
#   9xx = unknown/other
_SIGNATURE_MAP: dict[str, tuple[str, str]] = {
    "ToolInvocationRequested": ("100", "Tool Invocation Requested"),
    "ToolInvocationCompleted": ("101", "Tool Invocation Completed"),
    "ToolInvocationFailed": ("102", "Tool Invocation Failed"),
    "ProviderStarted": ("200", "Provider Started"),
    "ProviderStopped": ("201", "Provider Stopped"),
    "ProviderStateChanged": ("202", "Provider State Changed"),
    "ProviderDegraded": ("203", "Provider Degraded"),
    "ProviderIdleDetected": ("204", "Provider Idle Detected"),
    "HealthCheckPassed": ("300", "Health Check Passed"),
    "HealthCheckFailed": ("301", "Health Check Failed"),
    "ProviderDiscovered": ("210", "Provider Discovered"),
    "ProviderRegistered": ("211", "Provider Registered"),
    "ProviderDeregistered": ("212", "Provider Deregistered"),
}

# Map event types to CEF severity (0-10 scale).
# 0-3 = Low, 4-6 = Medium, 7-8 = High, 9-10 = Very-High
_SEVERITY_MAP: dict[str, int] = {
    "ToolInvocationRequested": 1,
    "ToolInvocationCompleted": 1,
    "ToolInvocationFailed": 7,
    "ProviderStarted": 1,
    "ProviderStopped": 3,
    "ProviderStateChanged": 3,
    "ProviderDegraded": 7,
    "ProviderIdleDetected": 1,
    "HealthCheckPassed": 1,
    "HealthCheckFailed": 6,
    "ProviderDiscovered": 1,
    "ProviderRegistered": 1,
    "ProviderDeregistered": 3,
}


def _escape_header(value: str) -> str:
    """Escape special characters in CEF header fields.

    In header fields, backslash and pipe must be escaped.

    Args:
        value: Raw header field value.

    Returns:
        Escaped string safe for CEF header.
    """
    return value.replace("\\", "\\\\").replace("|", "\\|")


def _escape_extension_value(value: str) -> str:
    """Escape special characters in CEF extension values.

    In extension values, backslash, equals sign, and newlines must be escaped.

    Args:
        value: Raw extension value.

    Returns:
        Escaped string safe for CEF extension value.
    """
    return (
        value.replace("\\", "\\\\")
        .replace("=", "\\=")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _format_cef_timestamp(dt: datetime) -> str:
    """Format a datetime as CEF-compatible timestamp.

    CEF uses millisecond-precision epoch or a specific date format.
    We use the epoch millis format for unambiguous parsing.

    Args:
        dt: Datetime to format.

    Returns:
        Millisecond epoch as string.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return str(int(dt.timestamp() * 1000))


def format_audit_record(record: AuditRecord) -> str:
    """Format an AuditRecord as a CEF string.

    Args:
        record: The audit record to format.

    Returns:
        A single CEF log line (no trailing newline).

    Example output:
        CEF:0|MCP Hangar|MCP Hangar|0.15.0|101|Tool Invocation Completed|1|
        rt=1700000000123 dvchost=mcp-hangar cs1=math cs1Label=ProviderID
        suser=alice cs2=agent-1 cs2Label=AgentID act=ToolInvocationCompleted
    """
    event_type = record.event_type
    sig_id, name = _SIGNATURE_MAP.get(event_type, ("999", event_type))
    severity = _SEVERITY_MAP.get(event_type, 5)

    # Build header
    header = "|".join([
        f"CEF:{CEF_VERSION}",
        _escape_header(DEVICE_VENDOR),
        _escape_header(DEVICE_PRODUCT),
        _escape_header(DEVICE_VERSION),
        _escape_header(sig_id),
        _escape_header(name),
        str(severity),
    ])

    # Build extension key=value pairs
    extensions: list[str] = []

    # Standard CEF fields
    extensions.append(f"rt={_format_cef_timestamp(record.occurred_at)}")
    extensions.append("dvchost=mcp-hangar")
    extensions.append(f"act={_escape_extension_value(event_type)}")

    # Event ID
    if record.event_id:
        extensions.append(f"externalId={_escape_extension_value(record.event_id)}")

    # Provider ID -> cs1 (custom string 1)
    if record.provider_id:
        extensions.append(f"cs1={_escape_extension_value(record.provider_id)}")
        extensions.append("cs1Label=ProviderID")

    # Identity fields
    if record.caller_user_id:
        extensions.append(f"suser={_escape_extension_value(record.caller_user_id)}")
    if record.caller_agent_id:
        extensions.append(f"cs2={_escape_extension_value(record.caller_agent_id)}")
        extensions.append("cs2Label=AgentID")
    if record.caller_session_id:
        extensions.append(f"cs3={_escape_extension_value(record.caller_session_id)}")
        extensions.append("cs3Label=SessionID")
    if record.caller_principal_type:
        extensions.append(f"cs4={_escape_extension_value(record.caller_principal_type)}")
        extensions.append("cs4Label=PrincipalType")

    # Tool name from data (common in tool invocation events)
    data = record.data or {}
    tool_name = data.get("tool_name")
    if tool_name:
        extensions.append(f"cs5={_escape_extension_value(str(tool_name))}")
        extensions.append("cs5Label=ToolName")

    # Duration from data
    duration_ms = data.get("duration_ms")
    if duration_ms is not None:
        extensions.append(f"cn1={duration_ms}")
        extensions.append("cn1Label=DurationMs")

    # Error type from data (for failed invocations)
    error_type = data.get("error_type")
    if error_type:
        extensions.append(f"reason={_escape_extension_value(str(error_type))}")

    return header + "|" + " ".join(extensions)


def format_audit_records(records: list[AuditRecord]) -> str:
    """Format multiple AuditRecords as newline-separated CEF strings.

    Args:
        records: List of audit records to format.

    Returns:
        Newline-separated CEF log lines.
    """
    return "\n".join(format_audit_record(r) for r in records)
