# Log Streaming

MCP Hangar captures stderr output from subprocess and Docker MCP servers and makes it available via REST API and WebSocket for real-time log viewing.

## Architecture

```
MCP Server Process (stderr)
        |
   stderr-reader thread
        |
   ProviderLogBuffer (ring buffer, 1000 lines)
        |
   +----+----+
   |         |
GET /logs  WebSocket /ws/mcp_servers/{id}/logs
   |         |
 REST API  LogStreamBroadcaster
```

Each MCP server gets a dedicated `ProviderLogBuffer` -- a thread-safe ring buffer holding the most recent 1000 log lines. A background reader thread continuously reads the MCP server's stderr and appends lines to the buffer.

## Log Line Format

Each log line is stored as a `LogLine` value object:

```json
{
  "timestamp": "2026-03-23T10:15:30.123456",
  "line": "INFO: Server started on port 8080",
  "mcp_server_id": "math",
  "stream": "stderr"
}
```

## REST API

### Get buffered logs

```bash
GET /api/mcp_servers/{mcp_server_id}/logs?lines=100
```

Returns the most recent `lines` entries from the ring buffer (default 100, max 1000).

**Response:**

```json
{
  "logs": [
    {"timestamp": "...", "line": "...", "mcp_server_id": "math", "stream": "stderr"},
    {"timestamp": "...", "line": "...", "mcp_server_id": "math", "stream": "stderr"}
  ],
  "mcp_server_id": "math",
  "count": 2
}
```

Returns an empty list if the MCP server exists but has no log buffer yet (MCP server not started). Returns 404 if the MCP server is not registered.

## WebSocket

### Live log stream

```
ws://localhost:8000/api/ws/mcp_servers/{mcp_server_id}/logs
```

Connects to a live stream of log lines for a specific MCP server. New lines are pushed as they arrive from stderr.

Each message is a JSON object with the same `LogLine` format:

```json
{"timestamp": "...", "line": "...", "mcp_server_id": "math", "stream": "stderr"}
```

The WebSocket connection uses `LogStreamBroadcaster`, which registers an `on_append` callback on the MCP server's log buffer. When the buffer receives a new line, the broadcaster pushes it to all connected WebSocket clients.

### Connection lifecycle

1. Client connects to `/api/ws/MCP servers/{mcp_server_id}/logs`.
2. Server validates that the MCP server exists.
3. Server registers a broadcast callback on the log buffer.
4. Log lines are pushed to the client as they arrive.
5. On disconnect, the callback is deregistered.

## Configuration

Log capture is automatic for subprocess and Docker MCP servers. No configuration is required.

| Behavior | Value |
|----------|-------|
| Buffer size | 1000 lines per MCP server |
| Captured stream | stderr only |
| Buffer type | Thread-safe ring buffer |
| Persistence | In-memory only (lost on restart) |

## Client Integration

Log streaming can be consumed by any HTTP/WebSocket client:

1. Fetch the initial buffer contents via `GET /api/mcp_servers/{id}/logs`.
2. Open a WebSocket connection for live updates.
3. Handle reconnection on WebSocket disconnect.

## Supported MCP Server Modes

| Mode | Log Capture | Notes |
|------|------------|-------|
| `subprocess` | stderr via PIPE | Reader thread reads `process.stderr` |
| `docker` | stderr via PIPE | `DockerLauncher` attaches to container stderr |
| `remote` | Not captured | Remote MCP servers manage their own logs |
