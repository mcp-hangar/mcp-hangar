# REST API Reference

Complete reference for all REST API endpoints exposed by MCP Hangar in HTTP mode.

**Base URL:** `http://localhost:8000/api`

All responses are JSON. Error responses return:

```json
{"error": "<ExceptionType>", "message": "<description>", "status_code": <code>}
```

---

## Providers

### List Providers

```
GET /providers?state={state}
```

| Parameter | In | Type | Required | Description |
|-----------|------|------|----------|-------------|
| `state` | query | string | No | Filter: `cold`, `ready`, `degraded`, `dead` |

**Response 200:**

```json
{
  "providers": [
    {
      "provider": "math",
      "state": "ready",
      "mode": "subprocess",
      "alive": true,
      "tools_count": 5,
      "health_status": "healthy",
      "tools_predefined": false,
      "description": "Math computation provider"
    }
  ]
}
```

### Create Provider

```
POST /providers
```

**Request body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `provider_id` | string | Yes | -- | Unique identifier |
| `mode` | string | Yes | -- | `subprocess`, `docker`, or `remote` |
| `command` | list[string] | For subprocess | -- | Command to run |
| `image` | string | For docker | -- | Docker image |
| `endpoint` | string | For remote | -- | HTTP endpoint URL |
| `env` | dict | No | `{}` | Environment variables |
| `idle_ttl_s` | int | No | `300` | Idle timeout in seconds |
| `health_check_interval_s` | int | No | `60` | Health check interval |
| `description` | string | No | -- | Human-readable description |
| `volumes` | list[string] | No | `[]` | Docker volume mounts |
| `network` | string | No | `"none"` | Docker network mode |
| `read_only` | bool | No | `true` | Read-only filesystem (docker) |

**Response 201:**

```json
{"provider_id": "math", "created": true}
```

### Get Provider

```
GET /providers/{provider_id}
```

**Response 200:** Provider detail object with tools, health, and configuration.

**Response 404:** Provider not found.

### Update Provider

```
PUT /providers/{provider_id}
```

**Request body (all fields optional):**

| Field | Type | Description |
|-------|------|-------------|
| `description` | string | New description |
| `env` | dict | New environment variables (replaces existing) |
| `idle_ttl_s` | int | New idle timeout |
| `health_check_interval_s` | int | New health check interval |

**Response 200:**

```json
{"provider_id": "math", "updated": true}
```

### Delete Provider

```
DELETE /providers/{provider_id}
```

Stops the provider if running, then removes it from the registry.

**Response 200:**

```json
{"provider_id": "math", "deleted": true}
```

### Start Provider

```
POST /providers/{provider_id}/start
```

**Response 200:** Start result object.

### Stop Provider

```
POST /providers/{provider_id}/stop
```

**Request body (optional):**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `reason` | string | `"user_request"` | Reason for stopping |

**Response 200:** Stop result object.

### Get Provider Tools

```
GET /providers/{provider_id}/tools
```

**Response 200:**

```json
{
  "tools": [
    {"name": "add", "description": "Add two numbers", "parameters": {...}}
  ]
}
```

### Get Provider Health

```
GET /providers/{provider_id}/health
```

**Response 200:** Health status object with check history.

### Get Provider Logs

```
GET /providers/{provider_id}/logs?lines={n}
```

| Parameter | In | Type | Default | Range | Description |
|-----------|------|------|---------|-------|-------------|
| `lines` | query | int | `100` | 1--1000 | Number of recent lines |

**Response 200:**

```json
{
  "logs": [
    {"timestamp": "2026-03-23T10:15:30", "line": "...", "provider_id": "math", "stream": "stderr"}
  ],
  "provider_id": "math",
  "count": 42
}
```

### Get Tool Invocation History

```
GET /providers/{provider_id}/tools/history?limit={n}&from_position={pos}
```

| Parameter | In | Type | Default | Range | Description |
|-----------|------|------|---------|-------|-------------|
| `limit` | query | int | `100` | 1--500 | Max records |
| `from_position` | query | int | `0` | -- | Event store version offset |

**Response 200:**

```json
{
  "provider_id": "math",
  "history": [...],
  "total": 42
}
```

---

## Groups

### List Groups

```
GET /groups
```

**Response 200:**

```json
{
  "groups": [
    {
      "group_id": "llm-pool",
      "state": "healthy",
      "strategy": "round_robin",
      "members": [...],
      "circuit_breaker_state": "closed"
    }
  ]
}
```

### Create Group

```
POST /groups
```

**Request body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `group_id` | string | Yes | -- | Unique identifier |
| `strategy` | string | No | `"round_robin"` | Load balancing strategy |
| `min_healthy` | int | No | `1` | Minimum healthy members |
| `description` | string | No | -- | Description |

**Response 201:**

```json
{"group_id": "llm-pool", "created": true}
```

### Get Group

```
GET /groups/{group_id}
```

**Response 200:** Group detail with members and circuit breaker state.

### Update Group

```
PUT /groups/{group_id}
```

**Request body (all optional):**

| Field | Type | Description |
|-------|------|-------------|
| `strategy` | string | New strategy |
| `min_healthy` | int | New minimum healthy count |
| `description` | string | New description |

**Response 200:**

```json
{"group_id": "llm-pool", "updated": true}
```

### Delete Group

```
DELETE /groups/{group_id}
```

**Response 200:**

```json
{"group_id": "llm-pool", "deleted": true}
```

### Rebalance Group

```
POST /groups/{group_id}/rebalance
```

Re-checks member health and resets circuit breaker if applicable.

**Response 200:**

```json
{"status": "rebalanced", "group_id": "llm-pool"}
```

### Add Group Member

```
POST /groups/{group_id}/members
```

**Request body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `member_id` | string | Yes | -- | Provider ID to add |
| `weight` | int | No | `1` | Routing weight |
| `priority` | int | No | `1` | Routing priority |

**Response 201:**

```json
{"group_id": "llm-pool", "provider_id": "llm-1", "added": true}
```

### Remove Group Member

```
DELETE /groups/{group_id}/members/{member_id}
```

**Response 200:**

```json
{"group_id": "llm-pool", "provider_id": "llm-1", "removed": true}
```

---

## Discovery

### List Sources

```
GET /discovery/sources
```

**Response 200:**

```json
{"sources": [{"source_id": "...", "type": "docker", "mode": "additive", "enabled": true, "last_scan": "..."}]}
```

### Register Source

```
POST /discovery/sources
```

**Request body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `source_type` | string | Yes | -- | `docker`, `filesystem`, `kubernetes`, `entrypoint` |
| `mode` | string | Yes | -- | `additive` or `authoritative` |
| `enabled` | bool | No | `true` | Activate immediately |
| `config` | dict | No | `{}` | Source-specific configuration |

**Response 201:**

```json
{"source_id": "...", "registered": true}
```

### Update Source

```
PUT /discovery/sources/{source_id}
```

**Request body (all optional):** `mode`, `enabled`, `config`.

**Response 200:**

```json
{"source_id": "...", "updated": true}
```

### Delete Source

```
DELETE /discovery/sources/{source_id}
```

**Response 200:**

```json
{"source_id": "...", "deregistered": true}
```

### Trigger Scan

```
POST /discovery/sources/{source_id}/scan
```

**Response 200:**

```json
{"source_id": "...", "scan_triggered": true, "providers_found": 3}
```

### Enable/Disable Source

```
PUT /discovery/sources/{source_id}/enable
```

**Request body:**

```json
{"enabled": true}
```

**Response 200:**

```json
{"source_id": "...", "enabled": true}
```

### List Pending Providers

```
GET /discovery/pending
```

**Response 200:**

```json
{"pending": [{"name": "new-provider", "source": "docker", "mode": "remote", ...}]}
```

### List Quarantined Providers

```
GET /discovery/quarantined
```

**Response 200:**

```json
{"quarantined": {...}}
```

### Approve Provider

```
POST /discovery/approve/{name}
```

**Response 200:** Approval result.

### Reject Provider

```
POST /discovery/reject/{name}
```

**Response 200:** Rejection result.

---

## Catalog

### List Entries

```
GET /catalog?search={query}&tags={tag1,tag2}
```

| Parameter | In | Type | Required | Description |
|-----------|------|------|----------|-------------|
| `search` | query | string | No | Substring search on name/description |
| `tags` | query | string | No | Comma-separated tags (AND logic) |

**Response 200:**

```json
{"entries": [...], "total": 10}
```

### Get Entry

```
GET /catalog/{entry_id}
```

**Response 200:** Catalog entry object.

### Add Entry

```
POST /catalog
```

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Provider name |
| `description` | string | Yes | Short description |
| `mode` | string | No | Default provider mode |
| `command` | list[string] | No | Default command |
| `image` | string | No | Default Docker image |
| `endpoint` | string | No | Default endpoint URL |
| `tags` | list[string] | No | Searchable tags |

**Response 201:** Created entry object.

### Delete Entry

```
DELETE /catalog/{entry_id}
```

**Response 200:** Deletion confirmation.

### Deploy Entry

```
POST /catalog/{entry_id}/deploy
```

Registers the catalog entry as a live provider via the CQRS pipeline.

**Response 201:** Created provider result.

---

## Configuration

### Get Config

```
GET /config
```

Returns the current server configuration with sensitive fields stripped.

**Response 200:**

```json
{"config": {"providers": [...]}}
```

### Reload Config

```
POST /config/reload
```

**Request body (optional):**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `config_path` | string | server default | Path to config file |
| `graceful` | bool | `true` | Graceful reload |

**Response 200:**

```json
{"status": "reloaded", "result": {...}}
```

### Export Config

```
POST /config/export
```

Serializes current in-memory state to YAML.

**Response 200:**

```json
{"yaml": "providers:\n  math:\n    mode: subprocess\n    ..."}
```

### Backup Config

```
POST /config/backup
```

Creates a rotating backup of the current configuration.

**Response 200:**

```json
{"backup_path": "/path/to/backup.yaml", "created": true}
```

### Config Diff

```
GET /config/diff
```

Compares on-disk configuration with current in-memory state.

**Response 200:**

```json
{"diff": "--- on-disk\n+++ in-memory\n@@ ...", "has_changes": true}
```

---

## System

### Get System Info

```
GET /system
```

**Response 200:**

```json
{
  "system": {
    "total_providers": 5,
    "providers_by_state": {"ready": 3, "cold": 2},
    "total_tools": 15,
    "total_tool_calls": 42,
    "uptime_seconds": 3600.5,
    "version": "0.12.0"
  }
}
```

---

## Auth Management

### Create API Key

```
POST /auth/keys
```

**Request body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `principal_id` | string | Yes | -- | Principal this key authenticates as |
| `name` | string | Yes | -- | Human-readable key name |
| `created_by` | string | No | `"system"` | Creator principal |
| `expires_at` | string | No | -- | ISO8601 expiry datetime |

**Response 201:**

```json
{"key_id": "...", "raw_key": "mcp_...", "principal_id": "...", "name": "..."}
```

!!! warning
    The `raw_key` is returned only once. Store it securely.

### Revoke API Key

```
DELETE /auth/keys/{key_id}
```

**Request body (optional):**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `revoked_by` | string | `"system"` | Revoking principal |
| `reason` | string | `""` | Revocation reason |

### List API Keys

```
GET /auth/keys/{principal_id}
```

### List All Roles

```
GET /auth/roles
```

### List Built-in Roles

```
GET /auth/roles/builtin
```

### Get Role

```
GET /auth/roles/{role_id}
```

### Create Custom Role

```
POST /auth/roles
```

### Update Custom Role

```
PUT /auth/roles/{role_id}
```

### Delete Custom Role

```
DELETE /auth/roles/{role_id}
```

### Assign Role

```
POST /auth/principals/{principal_id}/roles
```

**Request body:**

```json
{"role_id": "developer"}
```

### Revoke Role

```
DELETE /auth/principals/{principal_id}/roles/{role_id}
```

### List Principals

```
GET /auth/principals
```

### List Roles for Principal

```
GET /auth/principals/{principal_id}/roles
```

### Check Permission

```
POST /auth/check-permission
```

**Request body:**

```json
{"principal_id": "...", "permission": "providers:start"}
```

### Get Tool Access Policy

```
GET /auth/policies/{provider_id}/{tool_name}
```

### Set Tool Access Policy

```
PUT /auth/policies/{provider_id}/{tool_name}
```

**Request body:**

```json
{"principal_id": "...", "effect": "allow"}
```

### Clear Tool Access Policy

```
DELETE /auth/policies/{provider_id}/{tool_name}
```

---

## Observability

### Metrics

```
GET /observability/metrics
```

**Response 200:**

```json
{"prometheus_text": "# HELP mcp_hangar_...\n...", "summary": {"tool_calls_total": 42.0}}
```

### Audit Log

```
GET /observability/audit?provider_id={id}&event_type={type}&limit={n}
```

**Response 200:**

```json
{"records": [...], "total": 10}
```

### Security Events

```
GET /observability/security?limit={n}
```

**Response 200:**

```json
{"events": [...], "total": 5}
```

### Alert History

```
GET /observability/alerts?level={level}
```

| Parameter | In | Type | Required | Description |
|-----------|------|------|----------|-------------|
| `level` | query | string | No | `critical`, `warning`, or `info` |

### Metrics History

```
GET /observability/metrics/history?minutes={n}
```

Returns time-series snapshots of Prometheus metrics for charting.

---

## Maintenance

### Compact Event Stream

```
POST /maintenance/compact
```

Deletes events preceding the latest snapshot for a given stream.

**Request body:**

```json
{"stream_id": "provider:math"}
```

**Response 200:**

```json
{"compacted": {"stream_id": "provider:math", "events_deleted": 150}}
```

**Response 422:** Missing or empty `stream_id`.

**Response 500:** No snapshot exists for the stream.

---

## WebSocket Endpoints

### Events Stream

```
ws://host:port/api/ws/events
```

Streams all domain events as JSON frames.

### State Stream

```
ws://host:port/api/ws/state
```

Streams `ProviderStateChanged` events only.

### Provider Log Stream

```
ws://host:port/api/ws/providers/{provider_id}/logs
```

Streams stderr log lines for a specific provider.

See the [WebSockets guide](../guides/WEBSOCKETS.md) for connection details.
