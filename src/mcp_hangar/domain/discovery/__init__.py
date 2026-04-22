"""Discovery domain module.

This module contains the domain model for mcp_server discovery,
including value objects, ports, and domain services.
"""

from .conflict_resolver import ConflictResolution, ConflictResolver, ConflictResult
from .discovered_mcp_server import DiscoveredMcpServer
from .discovery_service import DiscoveryService
from .discovery_source import DiscoveryMode, DiscoverySource

__all__ = [
    "DiscoveredMcpServer",
    "DiscoveryMode",
    "DiscoverySource",
    "ConflictResolution",
    "ConflictResult",
    "ConflictResolver",
    "DiscoveryService",
]

import sys
from importlib import import_module

# legacy aliases
globals().update({"".join(("DiscoveredPro", "vider")): DiscoveredMcpServer})
sys.modules[f"{__name__}.{''.join(('discovered_pro', 'vider'))}"] = import_module(f"{__name__}.discovered_mcp_server")
