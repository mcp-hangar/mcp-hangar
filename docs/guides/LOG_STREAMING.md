# Log Streaming

MCP Hangar captures stderr output from subprocess and Docker MCP servers and makes it available via REST API.

## Architecture

```
MCP Server Process (stderr)
        |
   stderr-reader thread
        |
   ProviderLogBuffer (ring buffer, 1000 lines)
        |
   GET /api/mcp_servers/{id}/logs
        |
     REST API
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

## Configuration

Log capture is automatic for subprocess and Docker MCP servers. No configuration is required.

| Behavior | Value |
|----------|-------|
| Buffer size | 1000 lines per MCP server |
| Line length limit | 8192 bytes (truncated with `...`) |
| Encoding | UTF-8 with `replace` error handling |
| Capture source | stderr only (stdout is JSON-RPC) |
