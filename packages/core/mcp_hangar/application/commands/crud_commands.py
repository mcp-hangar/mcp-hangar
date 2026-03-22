"""CQRS command dataclasses for Provider and Group CRUD operations.

All commands are frozen dataclasses — immutable value objects that represent
a single intent to mutate state. Handlers receive these via the command bus.
"""

from dataclasses import dataclass, field

from .commands import Command


# =============================================================================
# Provider CRUD Commands
# =============================================================================


@dataclass(frozen=True)
class CreateProviderCommand(Command):
    """Create and register a new provider.

    Attributes:
        provider_id: Unique identifier for the new provider.
        mode: Provider mode ("subprocess", "docker", "remote").
        command: Subprocess command list (required for subprocess mode).
        image: Docker image name (required for docker mode).
        endpoint: HTTP endpoint URL (required for remote mode).
        env: Environment variables to pass to the provider.
        idle_ttl_s: Idle TTL in seconds before auto-shutdown.
        health_check_interval_s: Health check interval in seconds.
        description: Human-readable description / preprompt.
        source: Who is registering this provider ("api", "config", "discovery").
    """

    provider_id: str
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
class UpdateProviderCommand(Command):
    """Update mutable configuration fields on an existing provider.

    Only non-None fields are applied. Fields not specified are unchanged.

    Attributes:
        provider_id: Identifier of the provider to update.
        description: New human-readable description (optional).
        env: New environment variable dict, replaces existing (optional).
        idle_ttl_s: New idle TTL in seconds (optional).
        health_check_interval_s: New health check interval in seconds (optional).
        source: Who is updating this provider ("api", "config").
    """

    provider_id: str
    description: str | None = None
    env: dict[str, str] | None = None
    idle_ttl_s: int | None = None
    health_check_interval_s: int | None = None
    source: str = "api"


@dataclass(frozen=True)
class DeleteProviderCommand(Command):
    """Delete a provider, stopping it first if it is running.

    Attributes:
        provider_id: Identifier of the provider to delete.
        source: Who is deleting this provider ("api", "config").
    """

    provider_id: str
    source: str = "api"


# =============================================================================
# Group CRUD Commands
# =============================================================================


@dataclass(frozen=True)
class CreateGroupCommand(Command):
    """Create a new provider group.

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


@dataclass(frozen=True)
class AddGroupMemberCommand(Command):
    """Add a provider to an existing group.

    Attributes:
        group_id: Identifier of the group to add the member to.
        provider_id: Identifier of the provider to add.
        weight: Load balancing weight (higher = more traffic).
        priority: Member priority (lower = higher priority).
    """

    group_id: str
    provider_id: str
    weight: int = 1
    priority: int = 1


@dataclass(frozen=True)
class RemoveGroupMemberCommand(Command):
    """Remove a provider from a group.

    Attributes:
        group_id: Identifier of the group to remove the member from.
        provider_id: Identifier of the provider to remove.
    """

    group_id: str
    provider_id: str
