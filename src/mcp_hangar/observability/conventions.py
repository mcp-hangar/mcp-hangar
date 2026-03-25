"""MCP Hangar OpenTelemetry semantic conventions.

Defines stable attribute names for MCP governance telemetry.
These conventions ensure that spans, metrics, and logs emitted by
Hangar carry a consistent, machine-readable governance context that
partner backends (OpenLIT, Langfuse, Grafana, OTEL Collector) can
consume without Hangar-specific plugins.

Usage example::

    from opentelemetry import trace
    from mcp_hangar.observability.conventions import MCP, Enforcement, Provider

    tracer = trace.get_tracer("mcp_hangar")
    with tracer.start_as_current_span("tool.invoke") as span:
        span.set_attribute(Provider.ID, "my-provider")
        span.set_attribute(MCP.TOOL_NAME, "read_file")
        span.set_attribute(MCP.USER_ID, request.user_id)
        span.set_attribute(Enforcement.POLICY_RESULT, "allow")

Versioning:
    When adding new attributes, never rename or remove existing ones.
    Additions are backwards-compatible. Breaking changes require a new
    conventions module version (conventions_v2.py).

References:
    - OpenTelemetry Semantic Conventions: https://opentelemetry.io/docs/concepts/semantic-conventions/
    - PRODUCT_ARCHITECTURE.md Section 2: "MCP-aware OTEL semantic conventions"
"""


class Provider:
    """Attributes describing an MCP provider instance."""

    #: Unique provider identifier (e.g. "math-server", "code-interpreter").
    ID = "mcp.provider.id"

    #: Provider operational mode ("subprocess", "docker", "remote").
    MODE = "mcp.provider.mode"

    #: Provider lifecycle state ("COLD", "INITIALIZING", "READY", "DEGRADED", "DEAD").
    STATE = "mcp.provider.state"

    #: Provider group membership (group id or empty string if ungrouped).
    GROUP_ID = "mcp.provider.group_id"

    #: Container image reference (for docker mode providers).
    IMAGE = "mcp.provider.image"

    #: Whether the provider has a capability declaration ("true"/"false").
    HAS_CAPABILITIES = "mcp.provider.has_capabilities"

    #: Enforcement mode declared by the provider ("alert", "block", "quarantine").
    ENFORCEMENT_MODE = "mcp.provider.enforcement_mode"


class MCP:
    """Attributes describing an MCP tool invocation."""

    #: Tool name as advertised by the provider.
    TOOL_NAME = "mcp.tool.name"

    #: Tool call duration in milliseconds (use histogram metric instead when possible).
    TOOL_DURATION_MS = "mcp.tool.duration_ms"

    #: Result status of the tool call ("success", "error", "timeout", "blocked").
    TOOL_STATUS = "mcp.tool.status"

    #: MCP protocol session identifier.
    SESSION_ID = "mcp.session.id"

    #: Agent or client identifier making the tool call.
    AGENT_ID = "mcp.agent.id"

    #: Human user identity behind the agent request (if propagated).
    USER_ID = "mcp.user.id"

    #: Correlation ID for tracing multi-step agent workflows.
    CORRELATION_ID = "mcp.correlation_id"

    #: Whether the tool call was a cold start ("true"/"false").
    COLD_START = "mcp.tool.cold_start"

    #: Tool argument hash for audit purposes (do not store raw arguments).
    TOOL_ARGS_HASH = "mcp.tool.args_hash"

    #: Approximate token count consumed by the tool response (if available).
    RESPONSE_TOKENS = "mcp.tool.response_tokens"


class Enforcement:
    """Attributes describing policy and enforcement decisions."""

    #: Policy evaluation result ("allow", "deny", "quarantine").
    POLICY_RESULT = "mcp.enforcement.policy_result"

    #: Name or identifier of the policy that was evaluated.
    POLICY_NAME = "mcp.enforcement.policy_name"

    #: Category of enforcement action taken.
    #: Values: "none", "alert", "block", "quarantine", "rate_limit".
    ACTION = "mcp.enforcement.action"

    #: Violation type when a capability was exceeded.
    #: Values: "egress_undeclared", "tool_schema_drift", "resource_limit_exceeded", etc.
    VIOLATION_TYPE = "mcp.enforcement.violation_type"

    #: Destination involved in an egress violation (host:port).
    EGRESS_DESTINATION = "mcp.enforcement.egress_destination"

    #: Number of violations accumulated for this provider in this session.
    VIOLATION_COUNT = "mcp.enforcement.violation_count"

    #: Severity level of a violation ("critical", "high", "medium", "low").
    VIOLATION_SEVERITY = "mcp.enforcement.violation_severity"


class Audit:
    """Attributes for identity-aware audit trail entries."""

    #: Principal type ("api_key", "jwt", "oidc", "anonymous").
    PRINCIPAL_TYPE = "mcp.audit.principal_type"

    #: Principal identifier (API key ID, JWT sub claim, etc.).
    PRINCIPAL_ID = "mcp.audit.principal_id"

    #: Role(s) held by the principal at call time (comma-separated).
    PRINCIPAL_ROLES = "mcp.audit.principal_roles"

    #: Whether the request passed authentication ("true"/"false").
    AUTHENTICATED = "mcp.audit.authenticated"

    #: Whether the request passed authorization ("true"/"false").
    AUTHORIZED = "mcp.audit.authorized"

    #: Data sensitivity classification of the tool response.
    #: Values: "public", "internal", "confidential", "restricted".
    DATA_SENSITIVITY = "mcp.audit.data_sensitivity"


class Behavioral:
    """Attributes for behavioral profiling signals (enterprise)."""

    #: Whether this tool call matches a baseline pattern ("true"/"false").
    MATCHES_BASELINE = "mcp.behavioral.matches_baseline"

    #: Anomaly score for this tool call (0.0 = normal, 1.0 = highly anomalous).
    ANOMALY_SCORE = "mcp.behavioral.anomaly_score"

    #: Detection rule that matched, if any.
    RULE_ID = "mcp.behavioral.rule_id"

    #: Sequence position in a detected multi-step pattern.
    PATTERN_STEP = "mcp.behavioral.pattern_step"

    #: Name of the detected behavioral pattern.
    PATTERN_NAME = "mcp.behavioral.pattern_name"

    #: Type of behavioral deviation detected (new_destination, frequency_anomaly, etc.).
    DEVIATION_TYPE = "mcp.behavioral.deviation_type"


class Health:
    """Attributes for provider health check spans."""

    #: Health check result ("passed", "failed", "timeout").
    RESULT = "mcp.health.result"

    #: Number of consecutive health check failures.
    CONSECUTIVE_FAILURES = "mcp.health.consecutive_failures"

    #: Health check response time in milliseconds.
    DURATION_MS = "mcp.health.duration_ms"


# ---------------------------------------------------------------------------
# Metric names
# ---------------------------------------------------------------------------


class Metrics:
    """Standard metric names for MCP Hangar Prometheus / OTEL metrics.

    These names must match the metrics defined in src/mcp_hangar/metrics.py.
    """

    TOOL_CALLS_TOTAL = "mcp_hangar_tool_calls_total"
    TOOL_CALL_DURATION_SECONDS = "mcp_hangar_tool_call_duration_seconds"
    PROVIDER_STATE = "mcp_hangar_provider_state"
    COLD_STARTS_TOTAL = "mcp_hangar_cold_starts_total"
    HEALTH_CHECKS_TOTAL = "mcp_hangar_health_checks_total"
    CIRCUIT_BREAKER_STATE = "mcp_hangar_circuit_breaker_state"
    CAPABILITY_VIOLATIONS_TOTAL = "mcp_hangar_capability_violations_total"
    EGRESS_BLOCKED_TOTAL = "mcp_hangar_egress_blocked_total"
    PROVIDERS_QUARANTINED = "mcp_hangar_providers_quarantined"
    TOOL_SCHEMA_DRIFTS_TOTAL = "mcp_hangar_tool_schema_drifts_total"
    BEHAVIORAL_DEVIATIONS_TOTAL = "mcp_hangar_behavioral_deviations_total"


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def set_governance_attributes(
    span: object,
    *,
    provider_id: str,
    tool_name: str,
    mode: str | None = None,
    group_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    agent_id: str | None = None,
    policy_result: str | None = None,
    enforcement_action: str | None = None,
    cold_start: bool | None = None,
) -> None:
    """Set standard MCP governance attributes on an OTEL span in one call.

    Only attributes with non-None values are set. This avoids polluting
    OTLP backends with empty string attributes for optional governance fields.

    Args:
        span: OpenTelemetry span (or any object with set_attribute method).
        provider_id: Required. Provider identifier.
        tool_name: Required. Tool name as advertised by the provider.
        mode: Optional. Provider mode ("subprocess", "docker", "remote").
        group_id: Optional. Provider group identifier.
        user_id: Optional. Human user identity.
        session_id: Optional. MCP session identifier.
        agent_id: Optional. Agent or client identifier.
        policy_result: Optional. Policy evaluation result ("allow", "deny", "quarantine").
        enforcement_action: Optional. Enforcement action taken.
        cold_start: Optional. Whether this invocation triggered a cold start.
    """
    span.set_attribute(Provider.ID, provider_id)
    span.set_attribute(MCP.TOOL_NAME, tool_name)

    if mode is not None:
        span.set_attribute(Provider.MODE, mode)
    if group_id is not None:
        span.set_attribute(Provider.GROUP_ID, group_id)
    if user_id is not None:
        span.set_attribute(MCP.USER_ID, user_id)
    if session_id is not None:
        span.set_attribute(MCP.SESSION_ID, session_id)
    if agent_id is not None:
        span.set_attribute(MCP.AGENT_ID, agent_id)
    if policy_result is not None:
        span.set_attribute(Enforcement.POLICY_RESULT, policy_result)
    if enforcement_action is not None:
        span.set_attribute(Enforcement.ACTION, enforcement_action)
    if cold_start is not None:
        span.set_attribute(MCP.COLD_START, str(cold_start).lower())
