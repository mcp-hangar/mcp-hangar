"""Cloud connector for MCP Hangar.

Lightweight event forwarder that connects a standalone mcp-hangar instance
to Hangar Cloud via REST. Ships as an optional feature -- zero overhead
when not activated.

Usage:
    mcp-hangar serve --cloud-key hngr_lk_...

Or via config.yaml:
    cloud:
      enabled: true
      license_key: hngr_lk_...
"""

from .connector import CloudConnector
from .config import CloudConfig

__all__ = ["CloudConnector", "CloudConfig"]
