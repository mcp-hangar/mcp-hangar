# WebSockets

MCP Hangar provides a WebSocket endpoint for real-time streaming of domain events.

## Endpoint

| Endpoint | Description |
|----------|-------------|
| `/api/ws/events` | All domain events (filterable via subscribe message) |

## Connecting

Use any WebSocket client. Example with `websocat`:

```bash
# Stream all domain events
websocat ws://localhost:8000/api/ws/events
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

After connecting, send a JSON `subscribe` message to filter events:

```json
{
  "type": "subscribe",
  "event_types": ["McpServerStateChanged"],
  "mcp_server_ids": ["math"]
}
```

The server acknowledges with:

```json
{
  "type": "subscribed",
  "event_types": ["McpServerStateChanged"],
  "mcp_server_ids": ["math"]
}
```

You can update filters at any time by sending another `subscribe` message. Omit a key to not filter on that dimension (e.g., omit `mcp_server_ids` to receive events from all MCP servers).

## Queue and Backpressure

Each WebSocket connection has an internal message queue (`EventStreamQueue`). If a client falls behind:

1. Messages queue up to the buffer limit.
2. Beyond the limit, oldest messages are dropped.
3. The client receives a warning frame indicating dropped messages.

## Authentication

When auth is enabled, pass credentials via the initial HTTP upgrade headers:

```bash
websocat "ws://localhost:8000/api/ws/events" -H "X-API-Key: mcp_your_key_here"
```

## Connection Lifecycle

1. Client sends WebSocket upgrade to `/api/ws/events`.
2. Server accepts the connection.
3. (Optional) Client sends a `subscribe` message to set filters.
4. Server streams matching events as JSON messages.
5. Client can send updated `subscribe` messages at any time.
6. On disconnect, the subscription is cleaned up automatically.
