# REST API

MCP Hangar exposes a full REST API alongside the MCP protocol layer. The API is mounted at `/api/` when running in HTTP mode and provides CRUD operations, real-time WebSocket streams, and operational endpoints.

## Quick Start

Start Hangar in HTTP mode:

```bash
mcp-hangar serve --http --port 8000
```

The REST API is available at `http://localhost:8000/api/`.

```bash
# List all mcp_servers
curl http://localhost:8000/api/mcp_servers

# Get system info
curl http://localhost:8000/api/system

# Start a mcp_server
curl -X POST http://localhost:8000/api/mcp_servers/math/start
```

## Authentication

When authentication is enabled, pass your API key in the `X-API-Key` header:

```bash
curl -H "X-API-Key: mcp_your_key_here" http://localhost:8000/api/mcp_servers
```

See the [Authentication guide](AUTHENTICATION.md) for setup instructions.

## Endpoints Overview

All endpoints return JSON. Error responses follow the envelope format:

```json
{
  "error": "ProviderNotFoundError",
  "message": "MCP Server 'unknown' not found",
  "status_code": 404
}
```

### MCP servers

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/mcp_servers` | List all MCP servers (optional `?state=ready` filter) |
| `POST` | `/api/mcp_servers` | Create a new MCP server |
| `GET` | `/api/mcp_servers/{id}` | Get MCP server details |
| `PUT` | `/api/mcp_servers/{id}` | Update MCP server configuration |
| `DELETE` | `/api/mcp_servers/{id}` | Delete a MCP server (stops it first) |
| `POST` | `/api/mcp_servers/{id}/start` | Start a MCP server |
| `POST` | `/api/mcp_servers/{id}/stop` | Stop a MCP server |
| `GET` | `/api/mcp_servers/{id}/tools` | List MCP server tools |
| `GET` | `/api/mcp_servers/{id}/health` | Get health status |
| `GET` | `/api/mcp_servers/{id}/logs` | Get buffered log lines (`?lines=100`) |
| `GET` | `/api/mcp_servers/{id}/tools/history` | Tool invocation history |

### Groups

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/groups` | List all MCP server groups |
| `POST` | `/api/groups` | Create a new group |
| `GET` | `/api/groups/{id}` | Get group details |
| `PUT` | `/api/groups/{id}` | Update group configuration |
| `DELETE` | `/api/groups/{id}` | Delete a group |
| `POST` | `/api/groups/{id}/rebalance` | Trigger group rebalance |
| `POST` | `/api/groups/{id}/members` | Add a member to the group |
| `DELETE` | `/api/groups/{id}/members/{member_id}` | Remove a member |

### Discovery

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/discovery/sources` | List discovery sources |
| `POST` | `/api/discovery/sources` | Register a new source |
| `PUT` | `/api/discovery/sources/{id}` | Update a source |
| `DELETE` | `/api/discovery/sources/{id}` | Remove a source |
| `POST` | `/api/discovery/sources/{id}/scan` | Trigger immediate scan |
| `PUT` | `/api/discovery/sources/{id}/enable` | Enable/disable a source |
| `GET` | `/api/discovery/pending` | List MCP servers pending approval |
| `GET` | `/api/discovery/quarantined` | List quarantined MCP servers |
| `POST` | `/api/discovery/approve/{name}` | Approve a pending MCP server |
| `POST` | `/api/discovery/reject/{name}` | Reject a pending MCP server |

### Catalog

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/catalog` | List catalog entries (`?search=`, `?tags=`) |
| `GET` | `/api/catalog/{id}` | Get a catalog entry |
| `POST` | `/api/catalog` | Add a custom catalog entry |
| `DELETE` | `/api/catalog/{id}` | Remove a catalog entry |
| `POST` | `/api/catalog/{id}/deploy` | Deploy a catalog entry as a live MCP server |

### Configuration

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/config` | Get sanitized current configuration |
| `POST` | `/api/config/reload` | Trigger hot-reload from disk |
| `POST` | `/api/config/export` | Export current state as YAML |
| `POST` | `/api/config/backup` | Create a rotating config backup |
| `GET` | `/api/config/diff` | Diff on-disk vs in-memory configuration |

### System

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/system` | System info (uptime, version, metrics summary) |

### Auth Management

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/keys` | Create an API key |
| `DELETE` | `/api/auth/keys/{key_id}` | Revoke an API key |
| `GET` | `/api/auth/keys/{principal_id}` | List keys for a principal |
| `GET` | `/api/auth/roles` | List all roles |
| `GET` | `/api/auth/roles/builtin` | List built-in roles |
| `GET` | `/api/auth/roles/{role_id}` | Get role details |
| `POST` | `/api/auth/roles` | Create a custom role |
| `PUT` | `/api/auth/roles/{role_id}` | Update a custom role |
| `DELETE` | `/api/auth/roles/{role_id}` | Delete a custom role |
| `POST` | `/api/auth/principals/{id}/roles` | Assign a role to a principal |
| `DELETE` | `/api/auth/principals/{id}/roles/{role_id}` | Revoke a role |
| `GET` | `/api/auth/principals` | List principals |
| `GET` | `/api/auth/principals/{id}/roles` | List roles for a principal |
| `POST` | `/api/auth/check-permission` | Check if a principal has permission |
| `GET` | `/api/auth/policies/{MCP server}/{tool}` | Get tool access policy |
| `PUT` | `/api/auth/policies/{MCP server}/{tool}` | Set tool access policy |
| `DELETE` | `/api/auth/policies/{MCP server}/{tool}` | Clear tool access policy |

### Observability

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/observability/metrics` | Prometheus metrics + JSON summary |
| `GET` | `/api/observability/audit` | Audit log (`?mcp_server_id=`, `?event_type=`, `?limit=`) |
| `GET` | `/api/observability/security` | Security events |
| `GET` | `/api/observability/alerts` | Alert history (`?level=`) |
| `GET` | `/api/observability/metrics/history` | Time-series metrics snapshots |

### Maintenance

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/maintenance/compact` | Compact an event stream |

### WebSockets

| Path | Description |
|------|-------------|
| `/api/ws/events` | Real-time domain event stream |
| `/api/ws/state` | MCP server state change stream |
| `/api/ws/MCP servers/{id}/logs` | Live log stream for a MCP server |

See the [WebSockets guide](WEBSOCKETS.md) for connection details.

## Examples

### Create a MCP Server

```bash
curl -X POST http://localhost:8000/api/mcp_servers \
  -H "Content-Type: application/json" \
  -d '{
    "mcp_server_id": "my-llm",
    "mode": "subprocess",
    "command": ["python", "-m", "llm_server"],
    "idle_ttl_s": 600,
    "description": "LLM inference mcp_server"
  }'
```

```json
{"mcp_server_id": "my-llm", "created": true}
```

### Create a Group with Members

```bash
# Create group
curl -X POST http://localhost:8000/api/groups \
  -H "Content-Type: application/json" \
  -d '{
    "group_id": "llm-pool",
    "strategy": "round_robin",
    "min_healthy": 1
  }'

# Add members
curl -X POST http://localhost:8000/api/groups/llm-pool/members \
  -H "Content-Type: application/json" \
  -d '{"member_id": "my-llm", "weight": 1, "priority": 1}'
```

### Export and Diff Configuration

```bash
# Export current state
curl -X POST http://localhost:8000/api/config/export

# See what changed vs disk
curl http://localhost:8000/api/config/diff
```

### Register a Discovery Source

```bash
curl -X POST http://localhost:8000/api/discovery/sources \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "docker",
    "mode": "additive",
    "enabled": true,
    "config": {"socket_path": "/var/run/docker.sock"}
  }'
```

## CORS

CORS is configured via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_CORS_ORIGINS` | `*` | Allowed origins (comma-separated) |
| `MCP_CORS_METHODS` | `*` | Allowed methods |
| `MCP_CORS_HEADERS` | `*` | Allowed headers |

For production, restrict origins to your dashboard URL:

```bash
export MCP_CORS_ORIGINS="https://dashboard.example.com"
```

## Error Handling

All domain exceptions are mapped to HTTP status codes:

| Exception | Status Code |
|-----------|-------------|
| `ProviderNotFoundError` | 404 |
| `ValidationError` | 422 |
| `RateLimitExceeded` | 429 |
| `CompactionError` | 500 |
| Other `MCPError` | 500 |

The error envelope always contains `error` (exception class name), `message`, and `status_code`.

## Full Reference

For complete request/response schemas, see the [REST API Reference](../reference/rest-api.md).
