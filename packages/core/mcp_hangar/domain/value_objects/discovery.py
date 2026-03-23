"""Discovery source value objects.

Contains DiscoverySourceSpec — the immutable specification for a runtime-managed
discovery source with UUID-based identity.
"""

from dataclasses import dataclass, field
from typing import Any

from ..discovery.discovery_source import DiscoveryMode


@dataclass(frozen=True)
class DiscoverySourceSpec:
    """Immutable specification for a runtime-managed discovery source.

    Carries UUID-based identity (source_id) and full configuration for a
    discovery source managed by DiscoveryRegistry. Frozen dataclass — use
    dataclasses.replace() to produce updated copies.

    Attributes:
        source_id: UUID string uniquely identifying this spec.
        source_type: Source type string ("docker", "filesystem", "kubernetes", "entrypoint").
        mode: Discovery mode — additive or authoritative.
        enabled: Whether the source is currently active.
        config: Source-specific configuration dict (e.g. socket_path for docker).
    """

    source_id: str
    source_type: str
    mode: DiscoveryMode
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        source_id: str,
        source_type: str,
        mode: DiscoveryMode,
        enabled: bool = True,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize DiscoverySourceSpec with validation.

        Args:
            source_id: UUID string for this spec.
            source_type: Discovery source type key.
            mode: DiscoveryMode enum value.
            enabled: Whether the source is active.
            config: Source-specific configuration dict.

        Raises:
            ValueError: If source_id or source_type is empty.
        """
        if not source_id:
            raise ValueError("source_id cannot be empty")
        if not source_type:
            raise ValueError("source_type cannot be empty")
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "config", dict(config) if config else {})

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary for JSON responses.

        Returns:
            Dict with all fields; mode is serialized as its string value.
        """
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "mode": self.mode.value,
            "enabled": self.enabled,
            "config": self.config,
        }
