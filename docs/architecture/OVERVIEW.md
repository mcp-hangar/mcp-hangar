# Architecture

## Overview

MCP Hangar manages MCP providers with explicit lifecycle, health monitoring, and automatic cleanup.

MCP Hangar is organized as a monorepo:

| Package | Description | Location |
|---------|-------------|----------|
| **Core** | Python library (PyPI: `mcp-hangar`) | `packages/core/` |
| **Kubernetes Operator** | Go-based K8s operator | `packages/operator/` |
| **Helm Charts** | Deployment charts | `packages/helm-charts/` |

**Key concepts:**
- **Providers** — Subprocesses or containers exposing tools via JSON-RPC
- **State machine** — COLD → INITIALIZING → READY → DEGRADED → DEAD
- **Health monitoring** — Failure detection with circuit breaker
- **GC** — Automatic shutdown of idle providers

## State Machine

```
     COLD
       │ ensure_ready()
       ▼
  INITIALIZING
       │
       ├─► SUCCESS ──► READY
       │                 │ failures >= threshold
       │                 ▼
       │              DEGRADED
       │                 │ backoff + retry
       │                 └──► READY
       │
       └─► FAILURE ──► DEAD
                         │ retry < max
                         └──► INITIALIZING
```

## Components

```
┌─────────────────────────────────────────────────────────┐
│                    MCP Hangar                           │
│  (FastMCP server, registry.* tools)                    │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              Provider Manager                           │
│  - State machine    - Health tracking                  │
│  - Lock management  - Tool cache                       │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                Stdio Client                             │
│  - Message correlation    - Timeout management         │
│  - Reader thread          - JSON-RPC                   │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│           Provider Process                              │
│  (subprocess / docker / podman)                        │
└─────────────────────────────────────────────────────────┘

Background:
┌─────────────────────────────────────────────────────────┐
│  GC Worker: idle cleanup    Health Worker: checks      │
└─────────────────────────────────────────────────────────┘
```

## Threading

### Lock Hierarchy

Acquire in order to avoid deadlocks:
1. `Provider.lock` (per-provider)
2. `StdioClient.pending_lock` (per-client)

### Threads

| Thread | Purpose |
|--------|---------|
| Main | FastMCP server, tool calls |
| Reader (per provider) | Read stdout, dispatch responses |
| GC Worker | Idle provider cleanup |
| Health Worker | Periodic health checks |

### Critical Section

```python
# Fast path — check state without I/O
with lock:
    if state == READY and tool in cache:
        client = conn.client

# I/O outside lock
response = client.call(...)
```

## Error Handling

| Category | Strategy |
|----------|----------|
| Transient (timeout) | Retry with backoff |
| Permanent (not found) | Fail fast, mark DEAD |
| Provider (app error) | Propagate, track metrics |

### Circuit Breaker

```
READY (failures: 0)
  │ failure
READY (failures: N)
  │ threshold reached
DEGRADED (backoff)
  │ wait
COLD (retry eligible)
  │ ensure_ready()
READY (failures: 0)
```

## Message Correlation

```python
class StdioClient:
    pending: Dict[str, PendingRequest]

    def call(method, params, timeout):
        request_id = uuid4()
        queue = Queue(maxsize=1)
        pending[request_id] = PendingRequest(queue)

        write({"id": request_id, "method": method, ...})
        return queue.get(timeout=timeout)

    def _reader_loop():
        while not closed:
            msg = json.loads(read_stdout())
            pending.pop(msg["id"]).queue.put(msg)
```

## Health Checks

Uses `tools/list` — fast, standard, verifies full stack.

```python
class ProviderHealth:
    consecutive_failures: int
    last_success_at: float
    total_invocations: int
    total_failures: int
```

## Performance

**Hot path:**
```python
# Good — state check without I/O
with lock:
    if state == READY:
        client = conn.client
response = client.call(...)  # Outside lock

# Bad — I/O under lock
with lock:
    response = client.call(...)  # Blocks other threads
```

**Recommended TTL:**
- Subprocess: 180-300s
- Container: 300-600s
