"""CQRS command dataclasses for discovery source management.

All commands are frozen dataclasses — immutable value objects representing
a single intent to mutate DiscoveryRegistry state. UUID generation is the
handler's responsibility, not the command's.
"""

from dataclasses import dataclass, field
from typing import Any

from .commands import Command


@dataclass(frozen=True)
class RegisterDiscoverySourceCommand(Command):
    """Register a new discovery source in the registry.

    The handler generates a UUID for source_id — callers do not supply it.

    Attributes:
        source_type: Type of source ("docker", "filesystem", "kubernetes", "entrypoint").
        mode: Discovery mode string ("additive" or "authoritative").
        enabled: Whether the source is active immediately after registration.
        config: Source-specific configuration dict (e.g. socket_path for docker).
    """

    source_type: str
    mode: str
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UpdateDiscoverySourceCommand(Command):
    """Update mutable fields on an existing discovery source spec.

    Only non-None fields are applied. Fields not supplied remain unchanged.

    Attributes:
        source_id: UUID of the source spec to update.
        mode: New discovery mode string (optional).
        enabled: New enabled state (optional).
        config: New configuration dict — replaces entire config (optional).
    """

    source_id: str
    mode: str | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None


@dataclass(frozen=True)
class DeregisterDiscoverySourceCommand(Command):
    """Remove a discovery source from the registry.

    Attributes:
        source_id: UUID of the source spec to remove.
    """

    source_id: str


@dataclass(frozen=True)
class TriggerSourceScanCommand(Command):
    """Trigger an immediate discovery scan for a specific source.

    The handler delegates to DiscoveryOrchestrator.trigger_discovery().

    Attributes:
        source_id: UUID of the source spec to scan.
    """

    source_id: str


@dataclass(frozen=True)
class ToggleDiscoverySourceCommand(Command):
    """Enable or disable a discovery source.

    Attributes:
        source_id: UUID of the source spec to toggle.
        enabled: True to enable, False to disable.
    """

    source_id: str
    enabled: bool
