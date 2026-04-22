# WebSockets

MCP Hangar provides WebSocket endpoints for real-time streaming of domain events, MCP server state changes, and MCP server logs.

## Endpoints

All WebSocket endpoints are mounted under `/api/ws/`:

| Endpoint | Description |
|----------|-------------|
| `/api/ws/events` | All domain events |
| `/api/ws/state` | MCP server state changes only |
| `/api/ws/MCP servers/{id}/logs` | Live stderr log stream for a MCP server |

## Connecting

Use any WebSocket client. Examples with `websocat`:

```bash
# Stream all domain events
websocat ws://localhost:8000/api/ws/events

# Stream state changes only
websocat ws://localhost:8000/api/ws/state

# Stream logs for a specific mcp_server
websocat ws://localhost:8000/api/ws/mcp_servers/math/logs
```

## Event Stream (`/api/ws/events`)

Streams all domain events as they occur. Each message is a JSON object:

```json
{
  "event_type": "McpServerStarted",
  "mcp_server_id": "math",
  "mode": "subprocess",
  "tools_count": 5,
  "startup_duration_ms": 120,
  "timestamp": "2026-03-23T10:15:30.123456"
}
```

### Event Types

All events from `domain/events.py` are published:

- `McpServerStateChanged` -- State transition with old/new state.
- `McpServerStarted` -- MCP Server initialization complete.
- `McpServerStopped` -- MCP Server shut down.
- `McpServerDegraded` -- MCP Server marked as degraded.
- `HealthCheckPassed` / `HealthCheckFailed` -- Health check results.
- `ToolInvocationCompleted` / `ToolInvocationFailed` -- Tool call results.
- `ProviderDiscovered` / `ProviderRegistered` / `ProviderDeregistered` -- Discovery events.
- `CircuitBreakerStateChanged` -- Circuit breaker transitions.

### Filtering

The events endpoint supports optional query parameters for filtering:

```
ws://localhost:8000/api/ws/events?mcp_server_id=math
ws://localhost:8000/api/ws/events?event_type=McpServerStateChanged
```

## State Stream (`/api/ws/state`)

A filtered view of the event stream that only includes `McpServerStateChanged` events:

```json
{
  "event_type": "McpServerStateChanged",
  "mcp_server_id": "math",
  "old_state": "COLD",
  "new_state": "INITIALIZING",
  "timestamp": "2026-03-23T10:15:30.123456"
}
```

This endpoint can be used by clients to update MCP server state in real-time without polling.

## Log Stream (`/api/ws/MCP servers/{id}/logs`)

Streams stderr log lines for a specific MCP server. See the [Log Streaming guide](LOG_STREAMING.md) for details.

```json
{
  "timestamp": "2026-03-23T10:15:30.123456",
  "line": "INFO: Processing request...",
  "mcp_server_id": "math",
  "stream": "stderr"
}
```

## Connection Management

### WebSocket Manager

The `WebSocketManager` in `server/api/ws/manager.py` tracks all active connections. It provides:

- **Connection registry** -- Tracks active WebSocket connections per endpoint.
- **Broadcast** -- Publishes events to all connected clients for an endpoint.
- **Cleanup** -- Removes disconnected clients automatically.

### Event Bus Integration

The WebSocket layer subscribes to the `EventBus` for domain events. On server shutdown, subscriptions are cleaned up via `EventBus.unsubscribe_from_all()`.

### Queue and Backpressure

Each WebSocket connection has an internal message queue (`WebSocketQueue`). If a client falls behind:

1. Messages queue up to the buffer limit.
2. Beyond the limit, oldest messages are dropped.
3. The client receives a warning frame indicating dropped messages.

## Client Integration

WebSocket endpoints can be consumed by any WebSocket client:

### Example: `useWebSocket`

Base hook with exponential backoff reconnection:

```typescript
const { isConnected, lastMessage } = useWebSocket('/api/ws/events');
```

Features:

- Automatic reconnection with exponential backoff (1s, 2s, 4s, ... up to 30s).
- Connection state tracking.
- Message deserialization from JSON.

### `useEventStream`

High-level hook for the events endpoint:

```typescript
const { events } = useEventStream({ providerFilter: 'math' });
```

### `useMcpServerState`

Connects to the state endpoint and maintains a local map of MCP server states:

```typescript
const { providerStates } = useMcpServerState();
// providerStates: Record<string, McpServerState>
```

### `useProviderLogs`

Combines REST fetch with WebSocket for live logs:

```typescript
const { logs, isConnected } = useProviderLogs('math');
```

## State Management

WebSocket connection state is managed by a Zustand store (`src/store/websocket.ts`):

```typescript
interface WebSocketStore {
  connections: Record<string, WebSocketConnection>;
  connect: (url: string) => void;
  disconnect: (url: string) => void;
}
```

This enables clients to track connection status and reconnect all WebSocket connections after a network interruption.
