# pyright: reportExplicitAny=false

"""Batch invocation, hot load and configuration reload."""

from dataclasses import dataclass

from .base import DomainEvent


# Batch Invocation Events
# =============================================================================


@dataclass
class BatchInvocationRequested(DomainEvent):
    """Published when a batch invocation is requested."""

    batch_id: str
    call_count: int
    mcp_servers: list[str]
    max_concurrency: int
    timeout: float
    fail_fast: bool


@dataclass
class BatchInvocationCompleted(DomainEvent):
    """Published when a batch invocation completes."""

    batch_id: str
    total: int
    succeeded: int
    failed: int
    elapsed_ms: float
    cancelled: int = 0


@dataclass
class BatchCallCompleted(DomainEvent):
    """Published when a single call within a batch completes."""

    batch_id: str
    call_id: str
    call_index: int
    mcp_server_id: str
    tool_name: str
    success: bool
    elapsed_ms: float
    error_type: str | None = None


# =============================================================================
# Hot Load Events
# =============================================================================


@dataclass
class McpServerLoadAttempted(DomainEvent):
    """Published when a mcp_server load is attempted."""

    mcp_server_name: str
    user_id: str | None


@dataclass
class McpServerHotLoaded(DomainEvent):
    """Published when a mcp_server is successfully hot-loaded from the registry."""

    mcp_server_id: str
    mcp_server_name: str
    source: str
    verified: bool
    user_id: str | None
    tools_count: int
    load_duration_ms: float


@dataclass
class McpServerLoadFailed(DomainEvent):
    """Published when a mcp_server load fails."""

    mcp_server_name: str
    reason: str
    user_id: str | None
    error_type: str | None = None


@dataclass
class McpServerHotUnloaded(DomainEvent):
    """Published when a hot-loaded mcp_server is unloaded."""

    mcp_server_id: str
    user_id: str | None
    lifetime_seconds: float


# Configuration Reload Events


@dataclass
class ConfigurationReloadRequested(DomainEvent):
    """Published when configuration reload is requested."""

    config_path: str
    requested_by: str  # "sighup", "tool", "file_watcher"
    force: bool = False


@dataclass
class ConfigurationReloaded(DomainEvent):
    """Published when configuration is successfully reloaded."""

    config_path: str
    mcp_servers_added: list[str]
    mcp_servers_removed: list[str]
    mcp_servers_updated: list[str]
    mcp_servers_unchanged: list[str]
    reload_duration_ms: float
    requested_by: str


@dataclass
class ConfigurationReloadFailed(DomainEvent):
    """Published when configuration reload fails."""

    config_path: str
    reason: str
    error_type: str
    requested_by: str


# =============================================================================
