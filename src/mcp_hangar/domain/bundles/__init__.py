"""McpServer bundles - Curated collections of MCP mcp_servers.

Bundles provide sensible defaults for common use cases:
- Starter: Basic mcp_servers for general use
- Developer: McpServers for software development workflows
- Data: McpServers for data analysis and database access

Bundles are domain concepts that define which mcp_servers belong together.
"""

from .definitions import (
    Bundle,
    BUNDLES,
    DATA_BUNDLE,
    DEVELOPER_BUNDLE,
    get_all_bundles,
    get_bundle,
    get_mcp_server_definition,
    McpServerDefinition,
    STARTER_BUNDLE,
)
from .resolver import BundleResolver, resolve_bundles

__all__ = [
    "Bundle",
    "McpServerDefinition",
    "BUNDLES",
    "STARTER_BUNDLE",
    "DEVELOPER_BUNDLE",
    "DATA_BUNDLE",
    "get_bundle",
    "get_all_bundles",
    "get_mcp_server_definition",
    "BundleResolver",
    "resolve_bundles",
]
