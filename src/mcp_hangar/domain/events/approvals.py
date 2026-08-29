# pyright: reportExplicitAny=false

"""Human-in-the-loop approval and tool withdrawal."""

from dataclasses import dataclass

from ..value_objects.compat import accepts_legacy_provider_id
from .base import DomainEvent


# Approval Gate Events (v0.13.0 -- Human-in-the-Loop)
# =============================================================================


@accepts_legacy_provider_id
@dataclass
class ToolApprovalRequested(DomainEvent):
    """Published when a tool invocation is held pending human approval."""

    approval_id: str
    mcp_server_id: str
    tool_name: str = ""
    arguments_hash: str = ""
    channel: str = ""
    expires_at: str = ""
    correlation_id: str = ""


@accepts_legacy_provider_id
@dataclass
class ToolApprovalGranted(DomainEvent):
    """Published when a held tool invocation is approved by a human."""

    approval_id: str
    mcp_server_id: str
    tool_name: str = ""
    decided_by: str = ""
    decided_at: str = ""


@accepts_legacy_provider_id
@dataclass
class ToolApprovalDenied(DomainEvent):
    """Published when a held tool invocation is denied by a human."""

    approval_id: str
    mcp_server_id: str
    tool_name: str = ""
    decided_by: str = ""
    decided_at: str = ""
    reason: str | None = None


@accepts_legacy_provider_id
@dataclass
class ToolApprovalExpired(DomainEvent):
    """Published when a held tool invocation expires without a decision."""

    approval_id: str
    mcp_server_id: str
    tool_name: str = ""
    expired_at: str = ""


# =============================================================================
# Tool Withdrawal Events (#231 — call-path enforcement)
# =============================================================================


@dataclass
class ToolWithdrawn(DomainEvent):
    """Published when an operator withdraws a tool at runtime via the admin API.

    This is a runtime (process-local) withdrawal that survives config reloads.
    Fleet-wide propagation requires shared state (out of scope — issue #235).

    Attributes:
        tenant_id: Tenant for whom the tool is withdrawn, or ``None`` for ALL tenants.
        mcp_server: MCP server identifier owning the tool.
        tool: Name of the withdrawn tool.
        kind: ``tool``, ``prompt`` or ``resource`` -- which overlay the
            withdrawal was written to.
        schema_version: Event schema version.
    """

    tenant_id: str | None
    mcp_server: str
    tool: str
    kind: str = "tool"
    schema_version: int = 2


@dataclass
class ToolRestored(DomainEvent):
    """Published when an operator restores a runtime-withdrawn tool via the admin API.

    Affects ONLY the runtime overlay; a config-declared withdrawal
    independently persists (effective = config OR runtime).

    Attributes:
        tenant_id: Tenant for whom the tool is restored, or ``None`` meaning the
            entire runtime entry was removed.
        mcp_server: MCP server identifier owning the tool.
        tool: Name of the restored tool.
        kind: ``tool``, ``prompt`` or ``resource`` -- which overlay the
            withdrawal was written to.
        schema_version: Event schema version.
    """

    tenant_id: str | None
    mcp_server: str
    tool: str
    kind: str = "tool"
    schema_version: int = 2


@dataclass
class ToolWithdrawnRejected(DomainEvent):
    """Published when a tool call is rejected because the tool is withdrawn for the caller.

    Enforcement guarantee: **per-process-after-reload** — the registry is
    config-reload-driven (#230); withdrawal takes effect on the replica that
    reloaded. Fleet-wide synchronous withdrawal requires shared state (out of
    scope). Rejection is **envelope-level** (``CallResult(success=False)``);
    protocol-clean JSON-RPC ``-32601`` on a single ``tools/call`` is #232-gated.

    Attributes:
        tenant_id: Tenant whose call was rejected (may be None for anonymous callers).
        mcp_server: MCP server identifier owning the withdrawn tool.
        tool: Name of the withdrawn tool.
        schema_version: Event schema version.
    """

    tenant_id: str | None
    mcp_server: str
    tool: str
    schema_version: int = 1


# =============================================================================
