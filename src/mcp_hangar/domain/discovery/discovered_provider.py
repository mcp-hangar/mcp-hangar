"""Discovered Provider Value Object.

Value object representing a discovered provider with fingerprinting
and TTL-based lifecycle tracking.
"""

from dataclasses import dataclass
from datetime import datetime, UTC
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class DiscoveredProvider:
    """Value object representing a discovered provider.

    This immutable value object captures all information about a provider
    discovered from external sources (Kubernetes, Docker, filesystem, etc.).

    Attributes:
        name: Unique identifier for the provider
        source_type: Origin of discovery (kubernetes, docker, filesystem, entrypoint)
        mode: Connection mode (stdio, sse, http, subprocess)
        connection_info: Mode-specific connection details
        metadata: Labels, annotations, and custom data
        fingerprint: SHA256 hash for change detection
        discovered_at: First discovery timestamp
        last_seen_at: Most recent discovery timestamp (for TTL)
        ttl_seconds: Time-to-live before expiration (default: 90s = 3x refresh interval)
    """

    name: str
    source_type: str
    mode: str
    connection_info: dict[str, Any]
    metadata: dict[str, Any]
    fingerprint: str
    discovered_at: datetime
    last_seen_at: datetime
    ttl_seconds: int = 90

    @classmethod
    def create(
        cls,
        name: str,
        source_type: str,
        mode: str,
        connection_info: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int = 90,
    ) -> "DiscoveredProvider":
        """Factory method with automatic fingerprinting.

        Args:
            name: Unique identifier for the provider
            source_type: Origin of discovery
            mode: Connection mode
            connection_info: Mode-specific connection details
            metadata: Optional labels and annotations
            ttl_seconds: Time-to-live in seconds

        Returns:
            New DiscoveredProvider instance with computed fingerprint
        """
        metadata = metadata or {}
        fingerprint_data = json.dumps({"connection_info": connection_info, "metadata": metadata}, sort_keys=True)
        fingerprint = hashlib.sha256(fingerprint_data.encode()).hexdigest()[:16]
        now = datetime.now(UTC)

        return cls(
            name=name,
            source_type=source_type,
            mode=mode,
            connection_info=connection_info,
            metadata=metadata,
            fingerprint=fingerprint,
            discovered_at=now,
            last_seen_at=now,
            ttl_seconds=ttl_seconds,
        )

    def is_expired(self) -> bool:
        """Check if provider has exceeded TTL.

        Returns:
            True if time since last_seen exceeds ttl_seconds
        """
        now = datetime.now(UTC)
        # Handle both timezone-aware and naive datetimes
        last_seen = self.last_seen_at
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=UTC)
        elapsed = (now - last_seen).total_seconds()
        return elapsed > self.ttl_seconds

    def with_updated_seen_time(self) -> "DiscoveredProvider":
        """Return new instance with updated last_seen_at.

        Since this is an immutable value object, we create a new instance
        rather than mutating in place.

        Returns:
            New DiscoveredProvider with current timestamp as last_seen_at
        """
        return DiscoveredProvider(
            name=self.name,
            source_type=self.source_type,
            mode=self.mode,
            connection_info=self.connection_info,
            metadata=self.metadata,
            fingerprint=self.fingerprint,
            discovered_at=self.discovered_at,
            last_seen_at=datetime.now(UTC),
            ttl_seconds=self.ttl_seconds,
        )

    def has_changed(self, other: "DiscoveredProvider") -> bool:
        """Check if configuration has changed by comparing fingerprints.

        Args:
            other: Another DiscoveredProvider to compare

        Returns:
            True if fingerprints differ (configuration changed)
        """
        return self.fingerprint != other.fingerprint

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization.

        Returns:
            Dictionary representation of the provider
        """
        return {
            "name": self.name,
            "source_type": self.source_type,
            "mode": self.mode,
            "connection_info": self.connection_info,
            "metadata": self.metadata,
            "fingerprint": self.fingerprint,
            "discovered_at": self.discovered_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
            "ttl_seconds": self.ttl_seconds,
            "is_expired": self.is_expired(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DiscoveredProvider":
        """Create from dictionary representation.

        Args:
            data: Dictionary with provider data

        Returns:
            DiscoveredProvider instance
        """
        discovered_at = data.get("discovered_at")
        last_seen_at = data.get("last_seen_at")

        if isinstance(discovered_at, str):
            discovered_at = datetime.fromisoformat(discovered_at)
        if isinstance(last_seen_at, str):
            last_seen_at = datetime.fromisoformat(last_seen_at)

        return cls(
            name=data["name"],
            source_type=data["source_type"],
            mode=data["mode"],
            connection_info=data["connection_info"],
            metadata=data.get("metadata", {}),
            fingerprint=data["fingerprint"],
            discovered_at=discovered_at,
            last_seen_at=last_seen_at,
            ttl_seconds=data.get("ttl_seconds", 90),
        )

    def __str__(self) -> str:
        return f"DiscoveredProvider({self.name}, source={self.source_type}, mode={self.mode})"

    def __repr__(self) -> str:
        return (
            f"DiscoveredProvider(name={self.name!r}, source_type={self.source_type!r}, "
            f"mode={self.mode!r}, fingerprint={self.fingerprint!r})"
        )
