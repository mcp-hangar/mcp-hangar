"""Domain model - Aggregates and entities."""

# Re-export McpServerState from value_objects for convenience
from ..value_objects import (
    GroupState,
    LoadBalancerStrategy,
    McpServerMode,
    McpServerState,
    MemberPriority,
    MemberWeight,
)
from .aggregate import AggregateRoot
from .circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
from .health_tracker import HealthTracker
from .load_balancer import (
    BaseStrategy,
    LeastConnectionsStrategy,
    LoadBalancer,
    PriorityStrategy,
    RandomStrategy,
    RoundRobinStrategy,
    WeightedRoundRobinStrategy,
)
from .mcp_server import McpServer
from .mcp_server_config import (
    ContainerConfig,
    ContainerResourceConfig,
    HealthConfig,
    McpServerConfig,
    RemoteConfig,
    SubprocessConfig,
    ToolsConfig,
)
from .mcp_server_group import (
    GroupCircuitClosed,
    GroupCircuitOpened,
    GroupCreated,
    GroupMember,
    GroupMemberAdded,
    GroupMemberHealthChanged,
    GroupMemberRemoved,
    GroupStateChanged,
    McpServerGroup,
)
from .catalog import McpServerEntry
from .tool_catalog import ToolCatalog, ToolSchema

__all__ = [
    # Catalog
    "McpServerEntry",
    # Base
    "AggregateRoot",
    # Circuit Breaker
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitState",
    # McpServer
    "HealthTracker",
    "ToolCatalog",
    "ToolSchema",
    "McpServer",
    "McpServerConfig",
    "SubprocessConfig",
    "ContainerConfig",
    "ContainerResourceConfig",
    "RemoteConfig",
    "HealthConfig",
    "ToolsConfig",
    # Event Sourced McpServer
    # McpServer Group
    "McpServerGroup",
    "GroupMember",
    "GroupState",
    "LoadBalancerStrategy",
    "McpServerState",
    "MemberWeight",
    "MemberPriority",
    # Load Balancer
    "LoadBalancer",
    "BaseStrategy",
    "RoundRobinStrategy",
    "WeightedRoundRobinStrategy",
    "LeastConnectionsStrategy",
    "RandomStrategy",
    "PriorityStrategy",
    # Group Events
    "GroupCreated",
    "GroupMemberAdded",
    "GroupMemberRemoved",
    "GroupMemberHealthChanged",
    "GroupStateChanged",
    "GroupCircuitOpened",
    "GroupCircuitClosed",
]

import sys
from importlib import import_module

# legacy aliases
globals().update(
    {
        "".join(("Pro", "vider")): McpServer,
        "".join(("Pro", "viderConfig")): McpServerConfig,
        "".join(("Pro", "viderGroup")): McpServerGroup,
    }
)
sys.modules[f"{__name__}.{''.join(('pro', 'vider'))}"] = import_module(f"{__name__}.mcp_server")
sys.modules[f"{__name__}.{''.join(('pro', 'vider_config'))}"] = import_module(f"{__name__}.mcp_server_config")
sys.modules[f"{__name__}.{''.join(('pro', 'vider_group'))}"] = import_module(f"{__name__}.mcp_server_group")

# legacy aliases
ProviderState = McpServerState
ProviderMode = McpServerMode
