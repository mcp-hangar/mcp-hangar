# Architecture

## Overview

MCP Hangar is a production-grade system for managing MCP (Model Context Protocol) providers with explicit lifecycle management, health monitoring, and automatic resource cleanup.

### 30-Second Summary

**What it does:** MCP Hangar sits between your AI client (like LM Studio or Claude) and multiple MCP tool providers. Instead of configuring each provider separately, you configure them once in MCP Hangar, which handles starting, stopping, and monitoring them automatically.

**Key concepts:**
- **Providers** run as subprocesses or containers and expose tools via JSON-RPC
- **State machine** tracks each provider: COLD → INITIALIZING → READY → DEGRADED → DEAD
- **Health monitoring** detects failures and triggers recovery
- **Garbage collection** shuts down idle providers to save resources

**When to use this doc:** Read this if you want to understand how MCP Hangar works internally, customize its behavior, or contribute to the project. For basic usage, see the [README](../../README.md).

## Design Principles

1. **Explicit over Implicit**: State transitions are explicit, errors are structured
2. **Thread Safety First**: All shared state protected by locks
3. **Fail Fast, Recover Smart**: Detect failures immediately, recover with exponential backoff
4. **Observable by Default**: Structured logs, correlation IDs, health metrics
5. **Process-First**: Subprocess mode is primary, Docker is optional

## State Machine

```
     COLD (Initial)
       │
       │ ensure_ready()
       ▼
  INITIALIZING
       │
       ├─► SUCCESS ──► READY
       │                 │
       │                 │ health failures (>= threshold)
       │                 ▼
       │              DEGRADED
       │                 │
       │                 │ backoff elapsed, retry success
       │                 └──────► READY
       │
       └─► FAILURE ──► DEAD
                         │
                         │ retry (< max failures)
                         └──────► INITIALIZING
```

## Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                      MCP Hangar                              │
│  (FastMCP, exposes registry.* tools)                        │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│            Provider Manager (per provider)                   │
│  - State machine enforcement                                │
│  - Lock management                                          │
│  - Health tracking                                          │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│                  Stdio Client                                │
│  - Message correlation (request ID → response)              │
│  - Timeout management                                       │
│  - Reader thread                                            │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│            Provider Process (subprocess/docker)              │
│  - Implements MCP protocol                                  │
│  - Exposes tools via JSON-RPC                               │
└─────────────────────────────────────────────────────────────┘

Background Workers:
┌─────────────────────────────────────────────────────────────┐
│  - GC Worker: Shutdown idle providers                       │
│  - Health Worker: Active health checks                      │
└─────────────────────────────────────────────────────────────┘
```

## Threading Model

### Lock Hierarchy

To avoid deadlocks, locks must be acquired in this order:

1. `ProviderManager.conn.lock` (per-provider RLock)
2. `StdioClient.pending_lock` (per-client Lock)

Never hold multiple provider locks simultaneously.

### Thread Roles

| Thread | Purpose |
|--------|---------|
| Main | Runs FastMCP server, handles tool calls |
| Reader (per provider) | Reads stdout, dispatches responses |
| GC Worker | Periodic idle provider cleanup |
| Health Worker | Periodic health checks |

### Critical Sections

**Tool Invocation:**
```python
# Fast path - read-only check
with provider.conn.lock:
    if provider.conn.state == READY and tool in cache:
        client = provider.conn.client
        # Release lock before I/O

# I/O without lock
response = client.call("tools/call", {...})

# Update metrics
with provider.conn.lock:
    provider.conn.health.update(...)
```

## Error Handling

### Error Categories

| Category | Strategy | Example |
|----------|----------|---------|
| Transient | Retry with backoff | Timeout |
| Permanent | Fail fast, mark DEAD | Command not found |
| Provider | Propagate, track metrics | Division by zero |

### Circuit Breaker

```
READY (failures: 0)
  │ failure
  ▼
READY (failures: 1)
  │ failure
  ▼
READY (failures: 2)
  │ failure (threshold reached)
  ▼
DEGRADED (backoff: 8s)
  │ wait
  ▼
COLD (eligible for retry)
  │ ensure_ready()
  ▼
READY (failures: 0)
```

### Structured Exceptions

All exceptions carry context:

```python
class MCPError(Exception):
    message: str
    provider_id: str
    operation: str
    details: Dict[str, Any]
```

### Correlation IDs

Every tool invocation gets a UUID for request tracing through logs.

## Message Correlation

Multiple threads may call `client.call()` concurrently. The solution:

```python
class StdioClient:
    pending: Dict[str, PendingRequest]

    def call(method, params, timeout):
        request_id = uuid4()
        queue = Queue(maxsize=1)
        pending[request_id] = PendingRequest(request_id, queue, time.time())

        write({"id": request_id, "method": method, ...})
        response = queue.get(timeout=timeout)
        return response

    def _reader_loop():
        while not closed:
            msg = json.loads(read_stdout())
            pending_req = pending.pop(msg["id"])
            pending_req.queue.put(msg)
```

## Health Management

MCP Hangar uses **active** health checks:

```python
# Lightweight operation
response = client.call("tools/list", {}, timeout=5.0)
```

Why `tools/list`:
- Fast (no computation)
- Standard MCP method
- Verifies full RPC stack
- Refreshes tool cache

### Metrics Tracked

```python
class ProviderHealth:
    consecutive_failures: int
    last_success_at: float
    last_failure_at: float
    total_invocations: int
    total_failures: int
```

## Performance Optimization

### Hot Path

```python
# Good: Check state without starting
with lock:
    if state == READY and tool in cache:
        return invoke_cached(tool)

# Bad: Always ensure ready
with lock:
    ensure_ready()  # May do expensive I/O
```

### Lock Granularity

```python
# Good: Hold lock only for state access
with lock:
    client = conn.client
response = client.call(...)  # I/O outside lock

# Bad: Hold lock during I/O
with lock:
    response = client.call(...)  # Deadlock risk
```

### Resource Cleanup

```python
# GC Worker
for provider in providers:
    if now - provider.last_used > TTL:
        provider.shutdown()
```

Recommended TTL: 180-300s for subprocess, 300-600s for Docker.

## References

- [MCP Specification](https://modelcontextprotocol.io)
- [JSON-RPC 2.0](https://www.jsonrpc.org/specification)
- [Circuit Breaker Pattern](https://martinfowler.com/bliki/CircuitBreaker.html)
