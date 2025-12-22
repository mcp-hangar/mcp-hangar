# Domain-Driven Design Patterns

This document describes the DDD patterns implemented in MCP Hangar.

## Quick Reference

| Concept | Location | Purpose |
|---------|----------|---------|
| Aggregates | `domain/model/` | Manage entity lifecycle and consistency |
| Value Objects | `domain/value_objects.py` | Immutable, validated domain primitives |
| Domain Events | `domain/events.py` | Capture state changes for event-driven architecture |
| Exceptions | `domain/exceptions.py` | Structured domain errors |
| Repository | `domain/repository.py` | Abstract data access |
| Commands | `application/commands/` | Write operations (CQRS) |
| Queries | `application/queries/` | Read operations (CQRS) |
| Sagas | `application/sagas/` | Long-running processes |

## 30-Second Overview

**New to DDD?** Here's what you need to know:

1. **Domain Layer** (`mcp_hangar/domain/`) - Core business logic, independent of infrastructure
2. **Application Layer** (`mcp_hangar/application/`) - Orchestrates domain objects, handles use cases
3. **Infrastructure Layer** (`mcp_hangar/infrastructure/`) - External concerns (databases, buses, etc.)

The key principle: **Dependencies point inward**. Domain has no external dependencies, Application depends on Domain, Infrastructure depends on both.

---

## Layer Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Presentation Layer                          │
│                   (mcp_hangar/server.py)                        │
│              FastMCP tools exposed to clients                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Application Layer                            │
│               (mcp_hangar/application/)                         │
│  Commands, Queries, Event Handlers, Sagas, Read Models          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Domain Layer                               │
│                  (mcp_hangar/domain/)                           │
│  Aggregates, Entities, Value Objects, Events, Services          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Infrastructure Layer                          │
│               (mcp_hangar/infrastructure/)                      │
│  Event Bus, Command Bus, Query Bus, Event Store, Repositories   │
└─────────────────────────────────────────────────────────────────┘
```

## Aggregate Root: Provider

The `Provider` class manages MCP provider lifecycle.

**Location**: `mcp_hangar/domain/model/provider.py`

**Responsibilities**:
- Manage state machine (COLD → INITIALIZING → READY → DEGRADED → DEAD)
- Coordinate tool invocations
- Track health metrics
- Emit domain events

```python
class Provider(AggregateRoot):
    def ensure_ready(self) -> None:
        """Ensure provider is ready, starting if necessary."""

    def invoke_tool(self, tool_name: str, arguments: dict, timeout: float) -> dict:
        """Invoke a tool with proper error handling."""

    def health_check(self) -> bool:
        """Perform active health check."""

    def shutdown(self) -> None:
        """Gracefully shutdown the provider."""
```

## Value Objects

Immutable, validated domain primitives in `mcp_hangar/domain/value_objects.py`:

| Value Object | Purpose | Validation |
|--------------|---------|------------|
| `ProviderId` | Provider identifier | Alphanumeric, max 64 chars |
| `ToolName` | Tool identifier | Alphanumeric + dots, max 128 chars |
| `CorrelationId` | Request tracing | Valid UUID v4 |
| `ProviderState` | Lifecycle state | Enum: COLD, INITIALIZING, READY, DEGRADED, DEAD |
| `ProviderMode` | Execution mode | Enum: SUBPROCESS, DOCKER, REMOTE |
| `HealthStatus` | Health indicator | Enum: HEALTHY, DEGRADED, UNHEALTHY, UNKNOWN |
| `ProviderConfig` | Configuration | Validates mode-specific settings |
| `ToolArguments` | Tool arguments | Size limit, JSON-serializable |

```python
from mcp_hangar.domain.value_objects import ProviderId, ToolName

provider_id = ProviderId("my-provider")  # Validated automatically
tool_name = ToolName("calculator.add")   # Supports namespaced tools

if provider_id == "my-provider":  # String comparison supported
    print("Match!")
```

## Domain Events

Events in `mcp_hangar/domain/events.py`:

**Provider Lifecycle**:
- `ProviderStarted` - Provider successfully started
- `ProviderStopped` - Provider stopped (with reason)
- `ProviderDegraded` - Provider entered degraded state
- `ProviderStateChanged` - Any state transition

**Tool Invocation**:
- `ToolInvocationRequested` - Tool call initiated
- `ToolInvocationCompleted` - Tool call succeeded
- `ToolInvocationFailed` - Tool call failed

**Health**:
- `HealthCheckPassed` - Health check succeeded
- `HealthCheckFailed` - Health check failed
- `ProviderIdleDetected` - Provider detected as idle

```python
from mcp_hangar.domain.events import ProviderStarted

event = ProviderStarted(
    provider_id="math",
    mode="subprocess",
    tools_count=5,
    startup_duration_ms=123.45
)
```

## Domain Exceptions

Structured exceptions in `mcp_hangar/domain/exceptions.py`:

```
MCPError (base)
├── ProviderError
│   ├── ProviderNotFoundError
│   ├── ProviderStartError
│   ├── ProviderDegradedError
│   ├── CannotStartProviderError
│   ├── ProviderNotReadyError
│   └── InvalidStateTransitionError
├── ToolError
│   ├── ToolNotFoundError
│   ├── ToolInvocationError
│   └── ToolTimeoutError
├── ClientError
│   ├── ClientNotConnectedError
│   └── ClientTimeoutError
├── ValidationError
├── ConfigurationError
└── RateLimitExceeded
```

```python
from mcp_hangar.domain.exceptions import ProviderStartError

try:
    provider.ensure_ready()
except ProviderStartError as e:
    print(f"Failed: {e.message}")
    print(f"Provider: {e.provider_id}")
    print(f"Details: {e.details}")
```

## Repository Pattern

Interface in `mcp_hangar/domain/repository.py`:

```python
class IProviderRepository(ABC):
    @abstractmethod
    def get(self, provider_id: str) -> Optional[Provider]: ...

    @abstractmethod
    def add(self, provider_id: str, provider: Provider) -> None: ...

    @abstractmethod
    def get_all(self) -> Dict[str, Provider]: ...
```

Implementations:
- `InMemoryProviderRepository` - In-memory storage
- `EventSourcedProviderRepository` - Event sourcing with snapshots

## CQRS Pattern

Commands and Queries are separated for scalability.

### Commands

Write operations in `mcp_hangar/application/commands/`:
- `StartProviderCommand`
- `StopProviderCommand`
- `InvokeToolCommand`
- `HealthCheckCommand`

```python
from mcp_hangar.infrastructure.command_bus import get_command_bus, StartProviderCommand

command_bus = get_command_bus()
result = command_bus.send(StartProviderCommand(provider_id="math"))
```

### Queries

Read operations in `mcp_hangar/application/queries/`:
- `ListProvidersQuery`
- `GetProviderQuery`
- `GetProviderToolsQuery`

```python
from mcp_hangar.infrastructure.query_bus import get_query_bus, ListProvidersQuery

query_bus = get_query_bus()
providers = query_bus.execute(ListProvidersQuery(state_filter="ready"))
```

### Read Models

Optimized views in `mcp_hangar/application/read_models/`:
- `ProviderSummary` - Lightweight provider info
- `ProviderDetails` - Full provider details
- `SystemMetrics` - Aggregated system metrics

## Event Sourcing

Optional pattern for audit trails in `mcp_hangar/infrastructure/event_store.py`:

```python
store = get_event_store()

# Append events
store.append("provider-1", [event1, event2], expected_version=0)

# Load events
events = store.load("provider-1")

# Load from specific version
events = store.load("provider-1", from_version=5)
```

## Saga Pattern

Long-running processes in `mcp_hangar/application/sagas/`:

**ProviderRecoverySaga**:
- Automatic restart with exponential backoff
- Configurable max retries
- Alerts on max retries exceeded

**ProviderFailoverSaga**:
- Primary-to-backup failover
- Auto-failback support

```python
from mcp_hangar.application.sagas import ProviderRecoverySaga

saga = ProviderRecoverySaga(
    max_retries=5,
    base_delay_s=1.0,
    max_delay_s=60.0
)
saga_manager.register_event_saga(saga)
```

## Best Practices

**Use Value Objects**:
```python
provider_id = ProviderId("my-provider")  # Validated
provider.ensure_ready()
```

**Let Aggregates Emit Events**:
```python
provider.ensure_ready()
events = provider.collect_events()
for event in events:
    event_bus.publish(event)
```

**Use Commands for Writes**:
```python
command = StartProviderCommand(provider_id="math")
result = command_bus.send(command)
```

**Use Queries for Reads**:
```python
query = ListProvidersQuery(state_filter="ready")
providers = query_bus.execute(query)
```

**Handle Domain Exceptions**:
```python
try:
    result = provider.invoke_tool("add", {"a": 1, "b": 2})
except ToolNotFoundError:
    pass  # Tool doesn't exist
except ToolInvocationError as e:
    logger.error(f"Tool failed: {e.to_dict()}")
```

## Testing

```python
def test_provider_state_transition():
    provider = Provider(
        provider_id="test",
        mode="subprocess",
        command=["python", "server.py"]
    )
    assert provider.state == ProviderState.COLD

def test_provider_id_validation():
    with pytest.raises(ValueError):
        ProviderId("")  # Empty
    with pytest.raises(ValueError):
        ProviderId("a" * 100)  # Too long

def test_events_emitted_on_start():
    provider = Provider(...)
    provider.ensure_ready()
    events = provider.collect_events()
    assert any(isinstance(e, ProviderStarted) for e in events)
```
