# ADR-001: Command Query Responsibility Segregation (CQRS)

## Status

Accepted

## Date

2025-12-12

## Context

MCP Hangar needs to handle both write operations (starting/stopping providers, invoking tools) and read operations (listing providers, getting tool schemas, checking health). As the system grows, we need a clear separation between these concerns to:

1. Optimize read and write paths independently
2. Enable better scalability for read-heavy workloads
3. Improve testability by isolating side effects
4. Support eventual consistency patterns (event sourcing)

## Decision

We implement CQRS by separating commands (write operations) from queries (read operations) using dedicated bus infrastructure.

### Commands

Commands represent intent to change state. Each command is handled by exactly one handler.

**Location**: `mcp_hangar/application/commands/`

- `StartProviderCommand`
- `StopProviderCommand`
- `InvokeToolCommand`
- `HealthCheckCommand`
- `ShutdownIdleProvidersCommand`

### Queries

Queries represent read operations. They return data without side effects.

**Location**: `mcp_hangar/application/queries/`

- `ListProvidersQuery`
- `GetProviderQuery`
- `GetProviderToolsQuery`
- `GetProviderHealthQuery`
- `GetSystemMetricsQuery`

### Read Models

Optimized data structures for query results.

**Location**: `mcp_hangar/application/read_models/`

- `ProviderSummary` - Lightweight provider info for listing
- `ProviderDetails` - Full provider details
- `ToolInfo` - Tool schema information
- `HealthInfo` - Health metrics
- `SystemMetrics` - Aggregated system metrics

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        MCP Hangar                            │
│  ┌─────────────────┐              ┌─────────────────┐       │
│  │  Write Path     │              │   Read Path     │       │
│  │                 │              │                 │       │
│  │ registry.start  │              │ registry.list   │       │
│  │ registry.stop   │              │ registry.tools  │       │
│  │ registry.invoke │              │ registry.details│       │
│  └────────┬────────┘              └────────┬────────┘       │
│           │                                │                │
│           ▼                                ▼                │
│  ┌─────────────────┐              ┌─────────────────┐       │
│  │   Command Bus   │              │   Query Bus     │       │
│  └────────┬────────┘              └────────┬────────┘       │
│           │                                │                │
│           ▼                                ▼                │
│  ┌─────────────────┐              ┌─────────────────┐       │
│  │Command Handlers │              │ Query Handlers  │       │
│  │ (Modify state,  │              │ (Read-only,     │       │
│  │  emit events)   │              │  return models) │       │
│  └────────┬────────┘              └────────┬────────┘       │
│           │                                │                │
│           ▼                                ▼                │
│  ┌─────────────────┐              ┌─────────────────┐       │
│  │    Domain       │◄─────────────│  Read Models    │       │
│  │   Aggregates    │              │                 │       │
│  └─────────────────┘              └─────────────────┘       │
└─────────────────────────────────────────────────────────────┘
```

## Usage

### Command Bus

```python
from mcp_hangar.infrastructure.command_bus import get_command_bus, StartProviderCommand

command_bus = get_command_bus()
result = command_bus.send(StartProviderCommand(provider_id="math"))
```

### Query Bus

```python
from mcp_hangar.infrastructure.query_bus import get_query_bus, ListProvidersQuery

query_bus = get_query_bus()
providers = query_bus.execute(ListProvidersQuery(state_filter="ready"))
```

## Consequences

### Positive

- Clear separation of concerns
- Independent optimization of read/write paths
- Better testability
- Easy to add new commands/queries
- Natural audit points
- Event sourcing ready

### Negative

- More boilerplate code
- Learning curve for CQRS concepts
- Eventual consistency considerations

## References

- [Martin Fowler - CQRS](https://martinfowler.com/bliki/CQRS.html)
- [Microsoft - CQRS Pattern](https://docs.microsoft.com/en-us/azure/architecture/patterns/cqrs)
