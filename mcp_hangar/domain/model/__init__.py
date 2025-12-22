"""Domain model - Aggregates and entities."""

# Re-export ProviderState from value_objects for convenience
from ..value_objects import GroupState, LoadBalancerStrategy, MemberPriority, MemberWeight, ProviderState
from .aggregate import AggregateRoot
from .circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
from .event_sourced_provider import EventSourcedProvider, ProviderSnapshot
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
from .provider import Provider
from .provider_group import (
    GroupCircuitClosed,
    GroupCircuitOpened,
    GroupCreated,
    GroupMember,
    GroupMemberAdded,
    GroupMemberHealthChanged,
    GroupMemberRemoved,
    GroupStateChanged,
    ProviderGroup,
)
from .tool_catalog import ToolCatalog, ToolSchema

__all__ = [
    # Base
    "AggregateRoot",
    # Circuit Breaker
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitState",
    # Provider
    "HealthTracker",
    "ToolCatalog",
    "ToolSchema",
    "Provider",
    "ProviderState",
    "EventSourcedProvider",
    "ProviderSnapshot",
    # Provider Group
    "ProviderGroup",
    "GroupMember",
    "GroupState",
    "LoadBalancerStrategy",
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
