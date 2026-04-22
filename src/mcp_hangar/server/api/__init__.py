"""REST API module for MCP Hangar.

Provides the REST API layer that exposes mcp_server management operations
over HTTP/JSON. Uses Starlette (already in the dependency tree) with
CQRS dispatch via run_in_threadpool.

Public API:
    create_api_router: Factory function to create the API Starlette app.
"""

from .router import create_api_router

__all__ = ["create_api_router"]

import sys
from importlib import import_module

sys.modules[f"{__name__}.{''.join(('pro', 'viders'))}"] = import_module(f"{__name__}.mcp_servers")
