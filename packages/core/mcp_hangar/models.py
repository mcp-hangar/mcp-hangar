"""Core data models for the MCP registry with explicit state management.

This module provides backward compatibility imports for legacy code.
New code should import directly from the domain layer.

Deprecated imports (use domain layer instead):
- ProviderState -> from mcp_hangar.domain.value_objects import ProviderState
- MCPError, ProviderStartError, etc. -> from mcp_hangar.domain.exceptions import ...
- ToolSchema -> from mcp_hangar.domain.model import ToolSchema
"""

# Re-export all exceptions from the canonical location for backward compatibility
from .domain.exceptions import MCPError, ProviderNotFoundError, ProviderStartError

# Re-export ToolSchema from the canonical location
from .domain.model import ToolSchema

# Re-export ProviderState from the canonical location
from .domain.value_objects import ProviderState

__all__ = [
    "MCPError",
    "ProviderStartError",
    "ProviderNotFoundError",
    "ToolSchema",
    "ProviderState",
]
