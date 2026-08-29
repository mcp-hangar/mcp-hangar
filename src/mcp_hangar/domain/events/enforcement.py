# pyright: reportExplicitAny=false

"""Capability, egress and behavioural enforcement events."""

from dataclasses import dataclass, field
from typing import Any

from ..value_objects.compat import accepts_legacy_provider_id
from .base import DomainEvent


# Capability enforcement events (Phase 1 — PRODUCT_ARCHITECTURE.md)
# ---------------------------------------------------------------------------


@accepts_legacy_provider_id
@dataclass
class CapabilityViolationDetected(DomainEvent):
    """Published when a mcp_server exceeds its declared capabilities.

    Emitted by the enforcement engine whenever runtime behavior deviates
    from the capability declaration. The enforcement_action field records
    what Hangar did in response (alert/block/quarantine).

    Attributes:
        mcp_server_id: McpServer that violated its capabilities.
        violation_type: Category of violation. One of:
            "egress_undeclared" -- outbound connection to undeclared destination.
            "egress_blocked" -- blocked outbound connection.
            "filesystem_write" -- write to path not in write_paths.
            "filesystem_read" -- read from path not in read_paths.
            "env_undeclared" -- access to undeclared environment variable.
            "tool_count_exceeded" -- mcp_server advertised more tools than declared.
            "tool_schema_drift" -- tool schema changed between restarts.
            "resource_limit_exceeded" -- memory or CPU exceeded declared limit.
        violation_detail: Human-readable description with specifics.
        enforcement_action: What Hangar did: "alert", "block", or "quarantine".
        destination: For egress violations, the blocked/unexpected destination.
        schema_version: Event schema version.
    """

    mcp_server_id: str
    violation_type: str = ""
    violation_detail: str = ""
    enforcement_action: str = ""
    destination: str | None = None
    severity: str = "high"
    schema_version: int = 2
    # Process attribution (populated by the Tetragon backend / hangar-agent; #331/#333).
    process_pid: int | None = None
    container_id: str | None = None
    pod_name: str | None = None
    pod_namespace: str | None = None
    node_name: str | None = None


@accepts_legacy_provider_id
@dataclass
class EgressPolicyViolationObserved(DomainEvent):
    """Published when an Audit-mode MCPEgressPolicy would have blocked a call.

    In Audit mode (ADR-013) the L7 policy verdict is recorded but NOT enforced:
    the call proceeds. This event captures what Enforce mode *would* have done,
    so an operator can see the impact of a policy before switching it on.

    Attributes:
        mcp_server_id: McpServer whose tool call tripped the policy.
        tool_name: The MCP tool that was invoked.
        would_be_action: The verdict Enforce mode would have applied:
            "deny" or "require_approval".
        reasons: Human-readable reasons for the verdict (audit-friendly).
        correlation_id: Correlation id of the observed invocation, if any.
        identity_context: Caller identity context (tenant/subject), if any.
        policy_id: Content hash of the policy that produced the verdict
            (``L7Policy.policy_id``), so a record says *which* policy would have
            blocked and not only that one would (#1129). ``None`` on a verdict
            produced before this field existed.
        schema_version: Event schema version.
    """

    mcp_server_id: str
    tool_name: str = ""
    would_be_action: str = ""
    reasons: list[str] = field(default_factory=list)
    correlation_id: str | None = None
    identity_context: dict[str, Any] | None = None
    policy_id: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        # The hand-written constructor this replaces coerced None to [], and every
        # consumer iterates `reasons` without a None check.
        if self.reasons is None:
            self.reasons = []


@dataclass
class EgressPolicyEnforced(DomainEvent):
    """Published when an Enforce-mode MCPEgressPolicy actually refused a call.

    The sibling of :class:`EgressPolicyViolationObserved`, and it exists because
    the enforcing verdict used to be the least auditable one in the product
    (#1128). Audit mode -- which by definition changes nothing -- recorded an
    event, a warning and a metric; Enforce mode, refusing the call for real,
    recorded a ``logger.debug`` line carrying the generic caller-facing message,
    with the reason the policy computed dropped on the way out. An operator
    could not answer "which calls did this policy refuse yesterday" from
    anything Hangar wrote.

    The fields deliberately mirror the observed event, plus ``action``, so an
    exporter written for one works for the other and the two can be read as one
    series: what a policy *would* block in Audit and what it *did* block in
    Enforce are the same question asked either side of a switch.

    One difference from the observed event: no ``__post_init__`` coercing a
    ``None`` ``reasons`` to ``[]``. That one exists because it replaced a
    hand-written constructor that did ``x or []``; this event never had one, and
    its only producer passes a list.

    Attributes:
        mcp_server_id: McpServer whose tool call the policy refused.
        tool_name: The MCP tool that was invoked.
        action: The verdict applied: "deny" or "require_approval". The second is
            a refusal too: it is raised when the approval gate is not
            configured, refused, or timed out -- the fail-closed default.
        reasons: Human-readable reasons for the verdict (audit-friendly).
        correlation_id: Correlation id of the refused invocation, if any.
        identity_context: Caller identity context (tenant/subject), if any.
        policy_id: Content hash of the policy that produced the verdict (#1129).
        rule_kind: Which part of the policy decided -- "header", "tool" or
            "arguments". Carried as a field rather than left to be parsed out of
            ``reasons``, because it is what the counter labels and prose is not
            an interface.
        schema_version: Event schema version.
    """

    mcp_server_id: str
    tool_name: str = ""
    action: str = ""
    reasons: list[str] = field(default_factory=list)
    correlation_id: str | None = None
    identity_context: dict[str, Any] | None = None
    policy_id: str | None = None
    rule_kind: str = ""
    schema_version: int = 1


@dataclass
class EgressPolicySet(DomainEvent):
    """Published when an L7 egress policy is attached to or replaced on a server.

    Changing this policy changes what the enforcement plane will block, so the
    change itself belongs in the audit trail -- otherwise the only record that
    enforcement was narrowed or widened is a log line. The policy body is
    summarised rather than embedded: an auditor needs to see that enforcement
    moved and in which direction, and the full rule set can be large and is
    already retrievable from the server.

    Attributes:
        mcp_server_id: McpServer whose policy changed.
        source: Who set it -- "operator" (compiled from an MCPEgressPolicy) or
            "api" (the REST channel, which requires policy:write).
        mode: Policy mode as applied, "Enforce" or "Audit".
        default_action: Verdict for a tool no rule matches, "allow" or "deny".

    Note the casing difference between ``mode`` and ``default_action``: both
    carry their enum value verbatim, and ``PolicyMode`` is spelled in the CRD's
    capitalised form while ``ToolAction`` is lower-case. The values are passed
    through rather than normalised, so an audit record matches what the policy
    document says; normalising here would invent a third vocabulary.
        allow_rules: Number of allow globs in the policy.
        deny_rules: Number of deny globs.
        require_approval_rules: Number of globs gated on human approval.
        secret_pattern_groups: Secret-detection groups the policy activates.
        max_payload_bytes: Argument payload ceiling, if any.
        policy_id: Content hash of the policy now in force
            (``L7Policy.policy_id``). The same id the verdicts carry, so "which
            policy decided this call" and "when did that policy change" join on
            a value rather than on adjacent timestamps (#1129).
        schema_version: Event schema version.
    """

    mcp_server_id: str
    source: str
    mode: str
    default_action: str
    allow_rules: int = 0
    deny_rules: int = 0
    require_approval_rules: int = 0
    secret_pattern_groups: list[str] = field(default_factory=list)
    max_payload_bytes: int | None = None
    policy_id: str | None = None
    schema_version: int = 1


@dataclass
class EgressPolicyCleared(DomainEvent):
    """Published when an L7 egress policy is removed from a server.

    Separated from :class:`EgressPolicySet` because clearing is the direction
    that widens what the server may do, and an audit trail should not require
    reading a boolean field to notice that.

    Attributes:
        mcp_server_id: McpServer whose policy was removed.
        source: Who cleared it -- "operator" or "api".
        schema_version: Event schema version.
    """

    mcp_server_id: str
    source: str
    schema_version: int = 1


@accepts_legacy_provider_id
@dataclass
class EgressBlocked(DomainEvent):
    """Published when an outbound connection from a mcp_server is blocked.

    This is a specialization of CapabilityViolationDetected for the
    common case of network egress enforcement.

    Attributes:
        mcp_server_id: McpServer whose egress was blocked.
        destination_host: Blocked destination hostname or IP.
        destination_port: Blocked destination port.
        protocol: Connection protocol (tcp/udp/https/etc.).
        enforcement_source: "networkpolicy" (K8s) or "iptables" (Docker).
        schema_version: Event schema version.
    """

    mcp_server_id: str
    destination_host: str = ""
    destination_port: int = 0
    protocol: str = ""
    enforcement_source: str = "networkpolicy"
    schema_version: int = 1
    # Process attribution (populated by the Tetragon backend / hangar-agent; #331/#333).
    process_pid: int | None = None
    container_id: str | None = None
    pod_name: str | None = None
    pod_namespace: str | None = None
    node_name: str | None = None


@dataclass
class McpServerCapabilityQuarantined(DomainEvent):
    """Published when a mcp_server is quarantined due to capability violations.

    A quarantined mcp_server stops serving new requests until the operator
    reviews and releases it. Existing in-flight requests complete normally.

    Attributes:
        mcp_server_id: McpServer that was quarantined.
        reason: Human-readable reason for quarantine.
        violation_count: Number of violations that triggered quarantine.
        schema_version: Event schema version.
    """

    mcp_server_id: str
    reason: str
    violation_count: int = 1
    schema_version: int = 1


@dataclass
class McpServerCapabilityQuarantineReleased(DomainEvent):
    """Published when a capability-quarantined mcp_server is released by the operator.

    Attributes:
        mcp_server_id: McpServer released from quarantine.
        released_by: Identity of the operator who released the mcp_server.
        schema_version: Event schema version.
    """

    mcp_server_id: str
    released_by: str
    schema_version: int = 1


@accepts_legacy_provider_id
class ProviderCapabilityQuarantined(McpServerCapabilityQuarantined):
    """Deprecated alias for :class:`McpServerCapabilityQuarantined`, kept for pre-rename callers."""


@accepts_legacy_provider_id
class ProviderCapabilityQuarantineReleased(McpServerCapabilityQuarantineReleased):
    """Deprecated alias for :class:`McpServerCapabilityQuarantineReleased`, kept for pre-rename callers."""


@dataclass
class DigestMismatchEvent(DomainEvent):
    """Published when a tool's observed digest does not match the expected digest.

    Emitted by the digest validator during tool invocation or tool list refresh.
    The enforcement field records what action was taken (audit/warn/block).

    Attributes:
        mcp_server_id: McpServer that served the tool.
        tool_name: Name of the tool with mismatched digest.
        expected_digest: The digest from the allowlist, or None if tool had no entry.
        observed_digest: The computed digest of the tool's current schema.
        enforcement: The DigestEnforcement value applied.
        correlation_id: Request correlation ID for audit trail linkage.
        tenant_id: Tenant whose pin was evaluated (per-tenant pinning, #278); None
            for non-tenant-scoped digest checks.
        schema_version: Event schema version.
    """

    mcp_server_id: str
    tool_name: str
    expected_digest: str | None
    observed_digest: str | None
    enforcement: str
    correlation_id: str
    tenant_id: str | None = None
    schema_version: int = 1


# ---------------------------------------------------------------------------
# Mutator Events (SEP-1763)
# ---------------------------------------------------------------------------


@dataclass
class ResponseTruncated(DomainEvent):
    """Published when a mutator truncates an oversized response.

    Attributes:
        method: MCP method name (e.g. "tools/call").
        correlation_id: Request correlation ID.
        original_size: Payload size in bytes before truncation.
        truncated_size: Payload size in bytes after truncation.
        max_size: Configured maximum size that triggered truncation.
        schema_version: Event schema version.
    """

    method: str
    correlation_id: str
    original_size: int
    truncated_size: int
    max_size: int
    schema_version: int = 1


# ---------------------------------------------------------------------------
# Behavioral Profiling Events
# ---------------------------------------------------------------------------


@dataclass
class BehavioralDeviationDetected(DomainEvent):
    """Published when the deviation detector flags abnormal mcp_server behavior.

    Emitted during ENFORCING mode when an observation does not match the
    learned baseline profile. The deviation_type field classifies the
    deviation (new_destination, frequency_anomaly, protocol_drift).

    Follows the same pattern as CapabilityViolationDetected.

    Attributes:
        mcp_server_id: McpServer whose behavior deviated from baseline.
        deviation_type: Category of deviation (value from DeviationType enum).
        observed: Description of the observed behavior (e.g. "1.2.3.4:443/tcp").
        baseline_expected: Description of the baseline expectation.
        severity: Severity level ("critical", "high", "medium", "low").
        schema_version: Event schema version.
    """

    mcp_server_id: str
    deviation_type: str
    observed: str
    baseline_expected: str
    severity: str = "high"
    schema_version: int = 1


# ---------------------------------------------------------------------------
