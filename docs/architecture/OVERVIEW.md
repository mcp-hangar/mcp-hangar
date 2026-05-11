# Architecture

## Overview

MCP Hangar manages MCP servers with explicit lifecycle, health monitoring, and automatic cleanup.

MCP Hangar is organized as a monorepo:

| Package | Description | Location |
|---------|-------------|----------|
| **Core** | Python library (PyPI: `mcp-hangar`) | `src/mcp_hangar/` |
| **Enterprise** | BSL 1.1 licensed features | `enterprise/` |

**Key concepts:**

- **MCP servers** -- Subprocesses or containers exposing tools via JSON-RPC
- **State machine** -- COLD -> INITIALIZING -> READY -> DEGRADED -> DEAD
- **Health monitoring** -- Failure detection with circuit breaker
- **GC** -- Automatic shutdown of idle MCP servers
- **CQRS** -- Command/Query separation with domain events
- **Event Sourcing** -- Append-only event store for auditing and state reconstruction
- **Digest Pinning** -- SHA-256 tool digest verification (ADR-004)
- **Interceptor Framework** -- Pre/post hooks and response mutation pipeline (ADR-005)

## Layer Structure (DDD + CQRS)

The Python core follows Domain-Driven Design with strict layer separation:

```
src/mcp_hangar/
+-- domain/           Core business logic (NO external dependencies)
|   +-- model/        Aggregates: MCP Server, McpServerGroup
|   +-- events.py     Domain events
|   +-- exceptions.py Exception hierarchy
|   +-- value_objects/ McpServerId, McpServerMode, IdleTTL, etc.
|   +-- contracts/    Interfaces (IMetricsPublisher, IMcpServerRuntime)
|   +-- security/     Rate limiting, input validation
|
+-- application/      Use cases and orchestration
|   +-- commands/     Command handlers (CQRS write side)
|   +-- queries/      Query handlers (CQRS read side)
|   +-- sagas/        Long-running processes (recovery, failover)
|   +-- event_handlers/ React to domain events
|   +-- services/     Application services (TracedMcpServerService)
|   +-- ports/        Port interfaces (ObservabilityPort)
|
+-- infrastructure/   External concerns (implements domain contracts)
|   +-- discovery/    Docker, K8s, filesystem, entrypoint sources
|   +-- persistence/  Repositories, Event Store (SQLite, in-memory)
|   +-- registry/     Registry client
|   +-- event_bus.py  In-process event bus
|   +-- command_bus.py CQRS command dispatcher
|   +-- query_bus.py  CQRS query dispatcher
|
+-- server/           Protocol and transport layer
    +-- api/          REST API (Starlette routes)
    |   +-- ws/       WebSocket endpoint (events)
    +-- bootstrap/    DI composition root
    +-- cli/          CLI (typer-based)
    +-- tools/        MCP tool implementations
```

**Layer dependencies flow inward only:** Domain knows nothing about infrastructure. Infrastructure implements domain contracts. Server depends on all layers.

## System Architecture

```
+------------------------------------------------------------------+
|                    REST API (Starlette)                           |
|   /api/mcp_servers  /api/groups  /api/discovery  /api/ws/*         |
+----------------------------------+-------------------------------+
                                   |
+----------------------------------v-------------------------------+
|                    MCP Protocol Layer                             |
|             FastMCP server (stdio or HTTP transport)              |
|                    hangar_* MCP tools                             |
+----------------------------------+-------------------------------+
                                   |
+----------------------------------v-------------------------------+
|                    CQRS + Event Bus                               |
|   CommandBus -> Handlers   QueryBus -> Handlers   EventBus       |
+--------+-----------+-------------+-------------------------------+
         |           |             |
+--------v--+ +------v------+ +---v----+
|  MCP Server  | | McpServerGroup| |  Sagas  |
| Aggregate  | |  Aggregate   | |         |
+--------+---+ +------+------+ +---------+
         |           |
+--------v-----------v--------------------------------------------+
|                    Infrastructure                                |
|  StdioClient | DockerLauncher | EventStore | HealthTracker       |
|  Discovery Sources | Registry Client | Log Buffers               |
+------------------------------------------------------------------+
```

## State Machine

```
     COLD
       | ensure_ready()
       v
  INITIALIZING
       |
       +-> SUCCESS --> READY
       |                 | failures >= threshold
       |                 v
       |              DEGRADED
       |                 | reinitialize
       |                 +-> INITIALIZING
       |
       +-> FAILURE --> DEAD
                         | retry < max
                         +-> INITIALIZING
```

**Valid transitions:**

| From | To |
|------|----|
| COLD | INITIALIZING |
| INITIALIZING | READY, DEAD, DEGRADED |
| READY | COLD, DEAD, DEGRADED |
| DEGRADED | INITIALIZING, COLD |
| DEAD | INITIALIZING, DEGRADED |

There is no direct DEGRADED -> READY transition. Degraded MCP servers must reinitialize.

## CQRS Pattern

Commands modify state, queries read state. They never mix.

- **Commands**: `StartMcpServerCommand`, `CreateMcpServerCommand`, `CreateGroupCommand`, etc.
- **Queries**: `ListMcpServersQuery`, `GetMcpServerQuery`, `GetSystemMetricsQuery`, etc.
- **Events**: `McpServerStarted`, `ToolInvocationCompleted`, `McpServerHealthCheckFailed`, etc.

All state changes emit domain events via `AggregateRoot._record_event()`. Events are persisted to the Event Store for auditing and can be replayed. See [Event Sourcing](EVENT_SOURCING.md).

## Threading

### Lock Hierarchy

Acquire in order to avoid deadlocks (see `infrastructure/lock_hierarchy.py`):

```
PROVIDER(10) < PROVIDER_GROUP(11) < EVENT_BUS(20) < EVENT_STORE(30) < SAGA_MANAGER(40) < STDIO_CLIENT(50)
```

`TrackedLock` enforces this ordering at runtime.

### Threads

| Thread | Purpose |
|--------|---------|
| Main | FastMCP server, tool calls |
| Reader (per MCP server) | Read stdout, dispatch responses |
| Stderr Reader (per MCP server) | Capture stderr into log buffer |
| GC Worker | Idle MCP server cleanup |
| Health Worker | Periodic health checks |
| Metrics Snapshot Worker | Periodic metrics history capture |

### Safe I/O Pattern

```python
# Copy reference under lock, I/O outside lock
with lock:
    if state == READY:
        client = conn.client
response = client.call(...)  # Outside lock
```

## Error Handling

| Category | Strategy |
|----------|----------|
| Transient (timeout) | Retry with backoff |
| Permanent (not found) | Fail fast, mark DEAD |
| MCP Server (app error) | Propagate, track metrics |

### Circuit Breaker

MCP Server groups use a circuit breaker to isolate failing members:

- **CLOSED** -- Normal operation, failures tracked
- **OPEN** -- Requests rejected, backoff timer active
- **HALF_OPEN** -- Single test request allowed to probe recovery

## Performance

**Recommended TTL:**

- Subprocess: 180-300s
- Container: 300-600s
- Remote: 600+ (connection pooling)
