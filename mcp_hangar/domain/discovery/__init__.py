"""Discovery domain module.

This module contains the domain model for provider discovery,
including value objects, ports, and domain services.
"""

from .discovered_provider import DiscoveredProvider
from .discovery_source import DiscoveryMode, DiscoverySource
from .conflict_resolver import ConflictResolution, ConflictResult, ConflictResolver
from .discovery_service import DiscoveryService

__all__ = [
    "DiscoveredProvider",
    "DiscoveryMode",
    "DiscoverySource",
    "ConflictResolution",
    "ConflictResult",
    "ConflictResolver",
    "DiscoveryService",
]
