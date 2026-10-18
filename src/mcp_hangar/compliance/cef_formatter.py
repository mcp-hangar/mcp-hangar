"""CEF (Common Event Format) formatter for audit records.

Converts AuditRecord objects to ArcSight CEF strings for SIEM integration.

CEF format:
    CEF:Version|Device Vendor|Device Product|Device Version|Signature ID|Name|Severity|Extension

Reference: ArcSight Common Event Format (CEF) Rev 25.

This module is part of the compliance layer.
"""

from collections.abc import Mapping
from datetime import UTC, datetime

from mcp_hangar import __version__
from mcp_hangar.application.event_handlers.audit_handler import AuditRecord

from .refusal import CALLER_ROLES, ROUTE_BACKEND, TOOL_INVOCATION_DENIED, refusal_wire_fields

# CEF header constants
CEF_VERSION = "0"
DEVICE_VENDOR = "MCP Hangar"
DEVICE_PRODUCT = "MCP Hangar"
# The running release, not a literal: a literal stayed 0.15.0 from that
# release on, and a SIEM correlating by device version was told every gateway
# was a year old (#1705).
DEVICE_VERSION = __version__

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
    TOOL_INVOCATION_DENIED: ("103", "Tool Invocation Denied"),
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
    "ToolPinRecorded": ("410", "Tool Pin Recorded"),
    "ToolPinRevoked": ("411", "Tool Pin Revoked"),
}

# Map event types to CEF severity (0-10 scale).
# 0-3 = Low, 4-6 = Medium, 7-8 = High, 9-10 = Very-High
_SEVERITY_MAP: dict[str, int] = {
    "ToolInvocationRequested": 1,
    "ToolInvocationCompleted": 1,
    "ToolInvocationFailed": 7,
    # Medium: a policy refusing a call is the gateway working, and still worth a look.
    TOOL_INVOCATION_DENIED: 5,
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
    # A pin changing is a governance decision; a revoked one lifts a protection.
    "ToolPinRecorded": 3,
    "ToolPinRevoked": 5,
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
    return value.replace("\\", "\\\\").replace("=", "\\=").replace("\n", "\\n").replace("\r", "\\r")


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
        dt = dt.replace(tzinfo=UTC)
    return str(int(dt.timestamp() * 1000))


def _caller_cost_extensions(data: Mapping[str, object]) -> list[str]:
    """The caller and cost fields (#1705), in the dictionary's names where it has one.

    The caller id is `suid`, the source user id; the type is the labelled
    `flexString2`; the cost in hundredths of a cent and the input tokens are
    `cn2` and `cn3`, the output tokens the labelled `flexNumber1`. Every
    labelled string slot is taken (`cs1`-`cs6`, `flexString1`, `flexString2`),
    so the pricing model has no CEF field and is carried by the other three
    formats only. A zero cost is a value, not an absence.
    """
    extensions: list[str] = []
    if data.get("caller_id"):
        extensions.append(f"suid={_escape_extension_value(str(data['caller_id']))}")
    if data.get("caller_type"):
        extensions.append(f"flexString2={_escape_extension_value(str(data['caller_type']))}")
        extensions.append("flexString2Label=CallerType")
    for key, slot, label in (
        ("cost_cents", "cn2", "CostCents"),
        ("cost_input_tokens", "cn3", "CostInputTokens"),
        ("cost_output_tokens", "flexNumber1", "CostOutputTokens"),
    ):
        if data.get(key) is not None:
            extensions.append(f"{slot}={data[key]}")
            extensions.append(f"{slot}Label={label}")
    return extensions


def _pin_extensions(event_type: str, data: Mapping[str, object]) -> list[str]:
    """A tool pin's provenance (#1800): the pinned digest, the one it replaced, the approver.

    `fileHash`/`oldFileHash` are the dictionary's fields for a hash and the hash
    it replaced; the approver rides a labelled flex string.
    """
    if event_type not in ("ToolPinRecorded", "ToolPinRevoked"):
        return []
    out: list[str] = []
    if data.get("sha256"):
        out.append(f"fileHash={_escape_extension_value(str(data['sha256']))}")
    if data.get("previous_sha256"):
        out.append(f"oldFileHash={_escape_extension_value(str(data['previous_sha256']))}")
    approver = data.get("approved_by") or data.get("revoked_by")
    if approver:
        out.append(f"flexString2={_escape_extension_value(str(approver))}")
        out.append("flexString2Label=PinApprovedBy")
    return out


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
    header = "|".join(
        [
            f"CEF:{CEF_VERSION}",
            _escape_header(DEVICE_VENDOR),
            _escape_header(DEVICE_PRODUCT),
            _escape_header(DEVICE_VERSION),
            _escape_header(sig_id),
            _escape_header(name),
            str(severity),
        ]
    )

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
    # Tenant -> cs6, the one custom-string slot left (cs1-cs5 are taken)
    if record.tenant_id:
        extensions.append(f"cs6={_escape_extension_value(record.tenant_id)}")
        extensions.append("cs6Label=TenantID")

    data = record.data or {}
    # What authorized the call (#1347) -> spriv, the dictionary's field for the
    # privileges of the source user `suser` names.
    caller_roles = data.get(CALLER_ROLES)
    if caller_roles:
        extensions.append(f"spriv={_escape_extension_value(str(caller_roles))}")
    # The server a call was routed to -> flexString1 (#1594). cs1 is the target
    # the caller named, the group for a group call, and cs1-cs6 are all taken,
    # so the member goes in the dictionary's labelled flex string.
    route_backend = data.get(ROUTE_BACKEND)
    if route_backend:
        extensions.append(f"flexString1={_escape_extension_value(str(route_backend))}")
        extensions.append("flexString1Label=RouteBackend")

    extensions.extend(_caller_cost_extensions(data))

    # Tool name from data (common in tool invocation events)
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

    # A state change's transition (#1705). Every custom-string slot is taken,
    # so it goes in `msg`, CEF's free-text message; without it two transitions
    # of one server were byte-identical lines.
    from_state, to_state = data.get("from_state"), data.get("to_state")
    if from_state is not None and to_state is not None:
        extensions.append(f"msg={_escape_extension_value(f'{from_state} -> {to_state}')}")

    extensions.extend(_pin_extensions(event_type, data))

    # A refused call's bounded gate or L7 fields (#1582).
    extensions.extend(f"{key}={_escape_extension_value(value)}" for key, value in refusal_wire_fields(data))

    return header + "|" + " ".join(extensions)


def format_audit_records(records: list[AuditRecord]) -> str:
    """Format multiple AuditRecords as newline-separated CEF strings.

    Args:
        records: List of audit records to format.

    Returns:
        Newline-separated CEF log lines.
    """
    return "\n".join(format_audit_record(r) for r in records)
