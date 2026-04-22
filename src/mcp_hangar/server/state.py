"""Backward-compatible re-exports for server composition state.

New code should import shared runtime composition state from
``mcp_hangar.server.bootstrap.composition`` directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .bootstrap.composition import (
    GROUPS,
    RUNTIME_PROVIDERS,
    get_discovery_orchestrator,
    get_group_rebalance_saga,
    get_runtime,
    get_runtime_mcp_servers,
    set_discovery_orchestrator,
    set_group_rebalance_saga,
)

if TYPE_CHECKING:
    COMMAND_BUS: object
    EVENT_BUS: object
    INPUT_VALIDATOR: object
    PROVIDER_REPOSITORY: object
    PROVIDERS: object
    QUERY_BUS: object
    RATE_LIMIT_CONFIG: object
    RATE_LIMITER: object
    SECURITY_HANDLER: object


def __getattr__(name: str) -> object:
    """Delegate deprecated runtime-backed exports to composition state."""
    from .bootstrap.composition import __getattr__ as composition_getattr

    return composition_getattr(name)


__all__ = [
    "COMMAND_BUS",
    "EVENT_BUS",
    "GROUPS",
    "INPUT_VALIDATOR",
    "PROVIDER_REPOSITORY",
    "PROVIDERS",
    "QUERY_BUS",
    "RATE_LIMIT_CONFIG",
    "RATE_LIMITER",
    "RUNTIME_PROVIDERS",
    "SECURITY_HANDLER",
    "get_discovery_orchestrator",
    "get_group_rebalance_saga",
    "get_runtime",
    "get_runtime_mcp_servers",
    "set_discovery_orchestrator",
    "set_group_rebalance_saga",
]
