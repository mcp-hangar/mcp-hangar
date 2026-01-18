"""Discovery domain module.

This module contains the domain model for provider discovery,
including value objects, ports, and domain services.
"""

from .conflict_resolver import ConflictResolution, ConflictResolver, ConflictResult
from .discovered_provider import DiscoveredProvider
from .discovery_service import DiscoveryService
from .discovery_source import DiscoveryMode, DiscoverySource

__all__ = [
    "DiscoveredProvider",
    "DiscoveryMode",
    "DiscoverySource",
    "ConflictResolution",
    "ConflictResult",
    "ConflictResolver",
    "DiscoveryService",
]
