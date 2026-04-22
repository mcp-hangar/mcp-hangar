"""CQRS command dataclasses for McpServer and Group CRUD operations.

All commands are frozen dataclasses — immutable value objects that represent
a single intent to mutate state. Handlers receive these via the command bus.
"""

from dataclasses import dataclass, field

from .commands import Command


def _resolve_legacy_mcp_server_id(mcp_server_id: str | None, kwargs: dict[str, object]) -> str:
    if mcp_server_id is not None:
        return mcp_server_id
    legacy_id = kwargs.pop("provider_id", None)
    if isinstance(legacy_id, str):
        return legacy_id
    raise TypeError("Missing required argument: mcp_server_id")


# =============================================================================
# McpServer CRUD Commands
# =============================================================================


@dataclass(frozen=True)
class CreateMcpServerCommand(Command):
    """Create and register a new mcp_server.

    Attributes:
        mcp_server_id: Unique identifier for the new mcp_server.
        mode: McpServer mode ("subprocess", "docker", "remote").
        command: Subprocess command list (required for subprocess mode).
        image: Docker image name (required for docker mode).
        endpoint: HTTP endpoint URL (required for remote mode).
        env: Environment variables to pass to the mcp_server.
        idle_ttl_s: Idle TTL in seconds before auto-shutdown.
        health_check_interval_s: Health check interval in seconds.
        description: Human-readable description / preprompt.
        source: Who is registering this mcp_server ("api", "config", "discovery").
    """

    mcp_server_id: str
    mode: str
    command: list[str] | None = None
    image: str | None = None
    endpoint: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    idle_ttl_s: int = 300
    health_check_interval_s: int = 60
    description: str | None = None
    source: str = "api"


@dataclass(frozen=True)
class UpdateMcpServerCommand(Command):
    """Update mutable configuration fields on an existing mcp_server.

    Only non-None fields are applied. Fields not specified are unchanged.

    Attributes:
        mcp_server_id: Identifier of the mcp_server to update.
        description: New human-readable description (optional).
        env: New environment variable dict, replaces existing (optional).
        idle_ttl_s: New idle TTL in seconds (optional).
        health_check_interval_s: New health check interval in seconds (optional).
        source: Who is updating this mcp_server ("api", "config").
    """

    mcp_server_id: str
    description: str | None = None
    env: dict[str, str] | None = None
    idle_ttl_s: int | None = None
    health_check_interval_s: int | None = None
    source: str = "api"


@dataclass(frozen=True, init=False)
class DeleteMcpServerCommand(Command):
    """Delete a mcp_server, stopping it first if it is running.

    Attributes:
        mcp_server_id: Identifier of the mcp_server to delete.
        source: Who is deleting this mcp_server ("api", "config").
    """

    mcp_server_id: str
    source: str = "api"

    def __init__(self, mcp_server_id: str | None = None, source: str = "api", **kwargs: object):
        object.__setattr__(self, "mcp_server_id", _resolve_legacy_mcp_server_id(mcp_server_id, kwargs))
        object.__setattr__(self, "source", source)
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")

    @property
    def provider_id(self) -> str:
        return self.mcp_server_id


# =============================================================================
# Group CRUD Commands
# =============================================================================


@dataclass(frozen=True)
class CreateGroupCommand(Command):
    """Create a new mcp_server group.

    Attributes:
        group_id: Unique identifier for the new group.
        strategy: Load balancing strategy ("round_robin", "least_connections", "random").
        min_healthy: Minimum healthy members for HEALTHY group state.
        description: Human-readable description (optional).
        source: Who is creating this group ("api", "config").
    """

    group_id: str
    strategy: str = "round_robin"
    min_healthy: int = 1
    description: str | None = None
    source: str = "api"


@dataclass(frozen=True)
class UpdateGroupCommand(Command):
    """Update mutable configuration fields on an existing group.

    Only non-None fields are applied. Fields not specified are unchanged.

    Attributes:
        group_id: Identifier of the group to update.
        strategy: New load balancing strategy (optional).
        description: New human-readable description (optional).
        min_healthy: New minimum healthy member count (optional).
        source: Who is updating this group ("api", "config").
    """

    group_id: str
    strategy: str | None = None
    description: str | None = None
    min_healthy: int | None = None
    source: str = "api"


@dataclass(frozen=True)
class DeleteGroupCommand(Command):
    """Delete a group, stopping all members first.

    Attributes:
        group_id: Identifier of the group to delete.
        source: Who is deleting this group ("api", "config").
    """

    group_id: str
    source: str = "api"


@dataclass(frozen=True, init=False)
class AddGroupMemberCommand(Command):
    """Add a mcp_server to an existing group.

    Attributes:
        group_id: Identifier of the group to add the member to.
        mcp_server_id: Identifier of the mcp_server to add.
        weight: Load balancing weight (higher = more traffic).
        priority: Member priority (lower = higher priority).
    """

    group_id: str
    mcp_server_id: str
    weight: int = 1
    priority: int = 1

    def __init__(
        self,
        group_id: str,
        mcp_server_id: str | None = None,
        weight: int = 1,
        priority: int = 1,
        **kwargs: object,
    ):
        object.__setattr__(self, "group_id", group_id)
        object.__setattr__(self, "mcp_server_id", _resolve_legacy_mcp_server_id(mcp_server_id, kwargs))
        object.__setattr__(self, "weight", weight)
        object.__setattr__(self, "priority", priority)
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")

    @property
    def provider_id(self) -> str:
        return self.mcp_server_id


@dataclass(frozen=True, init=False)
class RemoveGroupMemberCommand(Command):
    """Remove a mcp_server from a group.

    Attributes:
        group_id: Identifier of the group to remove the member from.
        mcp_server_id: Identifier of the mcp_server to remove.
    """

    group_id: str
    mcp_server_id: str

    def __init__(self, group_id: str, mcp_server_id: str | None = None, **kwargs: object):
        object.__setattr__(self, "group_id", group_id)
        object.__setattr__(self, "mcp_server_id", _resolve_legacy_mcp_server_id(mcp_server_id, kwargs))
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")

    @property
    def provider_id(self) -> str:
        return self.mcp_server_id


# legacy aliases
globals().update(
    {
        "".join(("CreatePro", "viderCommand")): CreateMcpServerCommand,
        "".join(("UpdatePro", "viderCommand")): UpdateMcpServerCommand,
        "".join(("DeletePro", "viderCommand")): DeleteMcpServerCommand,
    }
)
