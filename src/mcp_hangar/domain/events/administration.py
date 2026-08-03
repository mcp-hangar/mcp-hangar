# pyright: reportExplicitAny=false

"""Server CRUD and RBAC management events."""

from dataclasses import dataclass

from ..value_objects.compat import (
    accepts_legacy_provider_id,
)
from .base import DomainEvent


# McpServer CRUD Events
# =============================================================================


@dataclass
class McpServerRegistered(DomainEvent):
    """Published when a mcp_server is registered via API, config, or discovery."""

    mcp_server_id: str
    source: str  # "api" | "config" | "discovery"
    mode: str

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerUpdated(DomainEvent):
    """Published when a mcp_server configuration is updated."""

    mcp_server_id: str
    source: str

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerDeregistered(DomainEvent):
    """Published when a mcp_server is deleted/deregistered."""

    mcp_server_id: str
    source: str

    def __post_init__(self):
        super().__init__()


@accepts_legacy_provider_id
class ProviderRegistered(McpServerRegistered):
    """Deprecated alias for :class:`McpServerRegistered`, kept for pre-rename callers."""


@accepts_legacy_provider_id
class ProviderUpdated(McpServerUpdated):
    """Deprecated alias for :class:`McpServerUpdated`, kept for pre-rename callers."""


@accepts_legacy_provider_id
class ProviderDeregistered(McpServerDeregistered):
    """Deprecated alias for :class:`McpServerDeregistered`, kept for pre-rename callers."""


# =============================================================================
# RBAC Management Events
# =============================================================================


@dataclass
class CustomRoleCreated(DomainEvent):
    """Published when a custom role is created."""

    role_name: str
    permissions: list[str]
    description: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class CustomRoleDeleted(DomainEvent):
    """Published when a custom role is deleted."""

    role_name: str
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class CustomRoleUpdated(DomainEvent):
    """Published when a custom role's permissions or description are updated."""

    role_name: str
    permissions: list[str]
    description: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class ToolAccessPolicySet(DomainEvent):
    """Published when a tool access policy is set for a scope/target."""

    scope: str  # "mcp_server", "group", or "member"
    target_id: str
    allow_list: list[str]
    deny_list: list[str]
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class ToolAccessPolicyCleared(DomainEvent):
    """Published when a tool access policy is removed for a scope/target."""

    scope: str
    target_id: str
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


# ---------------------------------------------------------------------------
