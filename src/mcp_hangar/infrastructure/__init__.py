"""Infrastructure layer - Technical implementations.

This package provides infrastructure components for:
- Command Bus: CQRS command dispatching
- Query Bus: CQRS query dispatching
- Event Bus: Publish/subscribe for domain events
- Event Store: Append-only event persistence
- Event Sourced Repository: Provider persistence via event sourcing
- Saga Manager: Long-running business process orchestration
- Lock Hierarchy: Thread safety with deadlock prevention

Note: Command classes (StartProviderCommand, etc.) have been moved to
application.commands to maintain proper layer separation.
"""

from .command_bus import CommandBus, CommandHandler, get_command_bus, reset_command_bus
from .event_bus import EventBus, EventHandler, get_event_bus, reset_event_bus
from .event_sourced_repository import EventSourcedProviderRepository, ProviderConfigStore
from .event_store import (
    ConcurrencyError,
    EventStore,
    EventStoreSnapshot,
    get_event_store,
    InMemoryEventStore,
    StoredEvent,
)
from .lock_hierarchy import (
    clear_thread_locks,
    get_current_thread_locks,
    LockLevel,
    LockOrderViolation,
    TrackedLock,
    TrackedRLock,
)
from .query_bus import (
    get_query_bus,
    GetProviderHealthQuery,
    GetProviderQuery,
    GetProviderToolsQuery,
    GetSystemMetricsQuery,
    ListProvidersQuery,
    Query,
    QueryBus,
    QueryHandler,
    reset_query_bus,
)
from .saga_manager import get_saga_manager, Saga, SagaContext, SagaManager, SagaState, SagaStep

__all__ = [
    # Command Bus
    "CommandBus",
    "CommandHandler",
    "get_command_bus",
    "reset_command_bus",
    # Event Bus
    "EventBus",
    "EventHandler",
    "get_event_bus",
    "reset_event_bus",
    # Query Bus
    "Query",
    "QueryBus",
    "QueryHandler",
    "ListProvidersQuery",
    "GetProviderQuery",
    "GetProviderToolsQuery",
    "GetProviderHealthQuery",
    "GetSystemMetricsQuery",
    "get_query_bus",
    "reset_query_bus",
    # Event Store
    "EventStore",
    "InMemoryEventStore",
    "StoredEvent",
    "EventStoreSnapshot",
    "ConcurrencyError",
    "get_event_store",
    # Event Sourced Repository
    "EventSourcedProviderRepository",
    "ProviderConfigStore",
    # Lock Hierarchy
    "LockLevel",
    "LockOrderViolation",
    "TrackedLock",
    "TrackedRLock",
    "get_current_thread_locks",
    "clear_thread_locks",
    # Saga
    "Saga",
    "SagaManager",
    "SagaState",
    "SagaStep",
    "SagaContext",
    "get_saga_manager",
]
