# MCP Tools Reference

Complete reference for all MCP protocol tools exposed by MCP Hangar. These tools are callable by any MCP client (Claude Desktop, LM Studio, custom integrations).

## Quick Reference

| Tool | Category | Description | Side Effects |
|------|----------|-------------|--------------|
| [`hangar_list`](#hangar_list) | Lifecycle | List all providers with state and tool counts | None (read-only) |
| [`hangar_start`](#hangar_start) | Lifecycle | Start a provider or group | Starts process/container |
| [`hangar_stop`](#hangar_stop) | Lifecycle | Stop a provider or group | Stops process/container |
| [`hangar_status`](#hangar_status) | Lifecycle | Human-readable health dashboard | None (read-only) |
| [`hangar_reload_config`](#hangar_reload_config) | Lifecycle | Reload configuration from disk | Stops/starts providers |
| [`hangar_load`](#hangar_load) | Hot-Loading | Load provider from registry at runtime | Downloads and starts provider |
| [`hangar_unload`](#hangar_unload) | Hot-Loading | Unload a hot-loaded provider | Stops and removes provider |
| [`hangar_tools`](#hangar_tools) | Provider | List tools available on a provider | May start cold provider |
| [`hangar_details`](#hangar_details) | Provider | Detailed provider or group information | None (read-only) |
| [`hangar_warm`](#hangar_warm) | Provider | Pre-start providers for faster first call | Starts provider processes |
| [`hangar_health`](#hangar_health) | Health | System-wide health summary | None (read-only) |
| [`hangar_metrics`](#hangar_metrics) | Health | Provider metrics in JSON or Prometheus format | None (read-only) |
| [`hangar_discover`](#hangar_discover) | Discovery | Trigger discovery scan across all sources | Updates pending provider list |
| [`hangar_discovered`](#hangar_discovered) | Discovery | List pending discovered providers | None (read-only) |
| [`hangar_quarantine`](#hangar_quarantine) | Discovery | List quarantined providers | None (read-only) |
| [`hangar_approve`](#hangar_approve) | Discovery | Approve a pending or quarantined provider | Registers provider |
| [`hangar_sources`](#hangar_sources) | Discovery | List discovery source status | None (read-only) |
| [`hangar_group_list`](#hangar_group_list) | Groups | List all provider groups with member details | None (read-only) |
| [`hangar_group_rebalance`](#hangar_group_rebalance) | Groups | Rebalance group membership and reset circuit breaker | Re-checks members, resets circuit |
| [`hangar_call`](#hangar_call) | Batch and Continuation | Invoke tools on providers (single or batch) | May start cold providers |
| [`hangar_fetch_continuation`](#hangar_fetch_continuation) | Batch and Continuation | Fetch truncated response data | None (read-only) |
| [`hangar_delete_continuation`](#hangar_delete_continuation) | Batch and Continuation | Delete cached continuation data | Removes cached response |

## Lifecycle

### `hangar_list` {#hangar_list}

List all configured providers, groups, and runtime (hot-loaded) providers with current state, mode, and tool counts.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `state_filter` | `str \| None` | `None` | Filter by state: `"cold"`, `"ready"`, `"degraded"`, `"dead"` |

**Side Effects:** None (read-only).

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `providers` | `list[object]` | Configured providers with `provider`, `state`, `mode`, `alive`, `tools_count`, `health_status`, `tools_predefined`, `description` |
| `groups` | `list[object]` | Groups with `group_id`, `state`, `strategy`, `healthy_count`, `total_members` |
| `runtime_providers` | `list[object]` | Hot-loaded providers with `provider`, `state`, `source`, `verified`, `ephemeral`, `loaded_at`, `lifetime_seconds` |

**Example:**

```json
// Request
{"state_filter": "ready"}

// Response
{
  "providers": [
    {"provider": "math", "state": "ready", "mode": "subprocess", "alive": true,
     "tools_count": 4, "health_status": "healthy", "tools_predefined": false}
  ],
  "groups": [],
  "runtime_providers": []
}
```

### `hangar_start` {#hangar_start}

Start a provider or group. Transitions the provider from COLD to READY.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `provider` | `str` | required | Provider ID or Group ID |

**Side Effects:** Starts provider process or container. State transitions from COLD to READY.

**Returns:**

For a provider:

| Field | Type | Description |
|-------|------|-------------|
| `provider` | `str` | Provider ID |
| `state` | `str` | New state (typically `"ready"`) |
| `tools` | `list[str]` | Available tool names |

For a group:

| Field | Type | Description |
|-------|------|-------------|
| `group` | `str` | Group ID |
| `state` | `str` | Group state |
| `members_started` | `int` | Number of members started |
| `healthy_count` | `int` | Healthy member count |
| `total_members` | `int` | Total member count |

**Example:**

```json
// Request
{"provider": "math"}

// Response
{"provider": "math", "state": "ready", "tools": ["add", "subtract", "multiply", "divide"]}
```

Errors: `ValueError("unknown_provider: <id>")`, `ValueError("unknown_group: <id>")`

### `hangar_stop` {#hangar_stop}

Stop a running provider or group.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `provider` | `str` | required | Provider ID or Group ID |

**Side Effects:** Stops provider process or container. State transitions to COLD.

**Returns:**

For a provider:

| Field | Type | Description |
|-------|------|-------------|
| `stopped` | `str` | Provider ID |
| `reason` | `str` | Stop reason |

For a group:

| Field | Type | Description |
|-------|------|-------------|
| `group` | `str` | Group ID |
| `state` | `str` | Group state |
| `stopped` | `bool` | `true` |

**Example:**

```json
// Request
{"provider": "math"}

// Response
{"stopped": "math", "reason": "manual_stop"}
```

Errors: `ValueError("unknown_provider: <id>")`

### `hangar_status` {#hangar_status}

Human-readable health dashboard with state indicators for all providers and groups.

**Parameters:** None.

**Side Effects:** None (read-only).

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `providers` | `list[object]` | Providers with `id`, `indicator`, `state`, `mode`, `last_used` |
| `groups` | `list[object]` | Groups with `id`, `indicator`, `state`, `healthy_members`, `total_members` |
| `runtime_providers` | `list[object]` | Hot-loaded providers with `id`, `indicator`, `state`, `source`, `verified` |
| `summary` | `object` | Counts: `healthy_providers`, `total_providers`, `runtime_providers`, `runtime_healthy`, `uptime`, `uptime_seconds` |
| `formatted` | `str` | Pre-formatted text dashboard |

Indicator values: `[READY]`, `[COLD]`, `[STARTING]`, `[DEGRADED]`, `[DEAD]`.

**Example:**

```json
// Request
{}

// Response
{
  "providers": [
    {"id": "math", "indicator": "[READY]", "state": "ready", "mode": "subprocess"}
  ],
  "groups": [],
  "runtime_providers": [],
  "summary": {"healthy_providers": 1, "total_providers": 1, "uptime": "2h 15m"},
  "formatted": "[READY] math (subprocess, 4 tools)"
}
```

### `hangar_reload_config` {#hangar_reload_config}

Reload configuration from disk, applying provider additions, removals, and updates.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `graceful` | `bool` | `true` | Wait for idle before stopping modified/removed providers |

**Side Effects:** Stops removed/modified providers, registers new providers, updates changed providers.

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `status` | `str` | `"success"` or `"failed"` |
| `message` | `str` | Human-readable result description |
| `providers_added` | `list[str]` | Newly added provider IDs |
| `providers_removed` | `list[str]` | Removed provider IDs |
| `providers_updated` | `list[str]` | Updated provider IDs |
| `providers_unchanged` | `list[str]` | Unchanged provider IDs |
| `duration_ms` | `float` | Reload duration in milliseconds |

On failure, the response includes `error_type` instead of provider lists.

**Example:**

```json
// Request
{"graceful": true}

// Response
{
  "status": "success", "message": "Configuration reloaded",
  "providers_added": ["new-api"], "providers_removed": [],
  "providers_updated": ["math"], "providers_unchanged": ["filesystem"],
  "duration_ms": 45.2
}
```

## Hot-Loading

### `hangar_load` (async) {#hangar_load}

Load a provider from the MCP registry at runtime. Hot-loaded providers are ephemeral and lost on server restart.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | required | Registry name of the provider |
| `force_unverified` | `bool` | `false` | Load unverified providers without confirmation |
| `allow_tools` | `list[str] \| None` | `None` | Fnmatch patterns for allowed tools |
| `deny_tools` | `list[str] \| None` | `None` | Fnmatch patterns for denied tools |

**Side Effects:** Downloads and starts the provider process. Adds to the runtime registry.

**Returns:**

The primary success response:

| Field | Type | Description |
|-------|------|-------------|
| `status` | `str` | `"loaded"` |
| `provider` | `str` | Assigned provider ID |
| `tools` | `list[str]` | Available tool names |

Other possible `status` values: `"ambiguous"` (multiple matches found, includes `matches` list), `"not_found"` (no match in registry), `"missing_secrets"` (required secrets not configured, includes `missing` list and `instructions`), `"unverified"` (provider not verified, use `force_unverified` to override), `"failed"` (configuration error).

**Example:**

```json
// Request
{"name": "filesystem", "allow_tools": ["read_*"]}

// Response
{"status": "loaded", "provider": "filesystem", "tools": ["read_file", "read_directory"]}
```

### `hangar_unload` {#hangar_unload}

Unload a hot-loaded provider. Only works for providers loaded via `hangar_load`.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `provider` | `str` | required | Provider ID (from `hangar_load` result) |

**Side Effects:** Stops the provider process and removes it from the runtime registry.

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `status` | `str` | `"unloaded"` or `"not_hot_loaded"` or `"failed"` |
| `provider` | `str` | Provider ID |
| `message` | `str` | Result description |
| `lifetime_seconds` | `float` | How long the provider was loaded (success only) |

**Example:**

```json
// Request
{"provider": "filesystem"}

// Response
{"status": "unloaded", "provider": "filesystem", "message": "Provider unloaded",
 "lifetime_seconds": 3600.5}
```

## Provider

### `hangar_tools` {#hangar_tools}

List the tools available on a provider or group. Tool access filtering (allow_list/deny_list) is applied.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `provider` | `str` | required | Provider ID or Group ID |

**Side Effects:** May start a cold provider to discover its tools.

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `provider` | `str` | Provider ID |
| `state` | `str` | Provider state |
| `predefined` | `bool` | Whether tools are predefined (not discovered at runtime) |
| `tools` | `list[object]` | Tools with `name`, `description`, `inputSchema` |

For groups, the response includes `group: true` instead of `predefined`.

**Example:**

```json
// Request
{"provider": "math"}

// Response
{
  "provider": "math", "state": "ready", "predefined": false,
  "tools": [
    {"name": "add", "description": "Add two numbers",
     "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}}}
  ]
}
```

Errors: `ValueError("unknown_provider: <id>")`, `ValueError("no_healthy_members_in_group: <id>")`

### `hangar_details` {#hangar_details}

Detailed information about a provider or group, including health tracking, idle time, and tool access policy.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `provider` | `str` | required | Provider ID or Group ID |

**Side Effects:** None (read-only).

**Returns:**

For a provider:

| Field | Type | Description |
|-------|------|-------------|
| `provider` | `str` | Provider ID |
| `state` | `str` | Current state |
| `mode` | `str` | Provider mode |
| `alive` | `bool` | Whether the provider process is running |
| `tools` | `list[object]` | Tool list with schemas |
| `health` | `object` | Health tracking: `consecutive_failures`, `last_check`, etc. |
| `idle_time` | `float \| None` | Seconds since last use |
| `meta` | `object` | Provider metadata |
| `tools_policy` | `object` | Tool access policy: `type`, `has_allow_list`, `has_deny_list`, `filtered_count` |

For a group:

| Field | Type | Description |
|-------|------|-------------|
| `group_id` | `str` | Group ID |
| `description` | `str \| None` | Group description |
| `state` | `str` | Group state |
| `strategy` | `str` | Load balancing strategy |
| `min_healthy` | `int` | Minimum healthy members |
| `healthy_count` | `int` | Current healthy member count |
| `total_members` | `int` | Total member count |
| `is_available` | `bool` | Whether the group can accept requests |
| `circuit_open` | `bool` | Whether the circuit breaker is open |
| `members` | `list[object]` | Members with `id`, `state`, `in_rotation`, `weight`, `priority`, `consecutive_failures` |

**Example:**

```json
// Request
{"provider": "math"}

// Response
{
  "provider": "math", "state": "ready", "mode": "subprocess", "alive": true,
  "tools": [{"name": "add", "description": "Add two numbers"}],
  "health": {"consecutive_failures": 0, "last_check": "2026-01-15T10:30:00Z"},
  "idle_time": 45.2,
  "tools_policy": {"type": "open", "has_allow_list": false, "has_deny_list": false}
}
```

Errors: `ValueError("unknown_provider: <id>")`

### `hangar_warm` {#hangar_warm}

Pre-start one or more providers so the first tool call does not incur cold-start latency. Groups are skipped.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `providers` | `str \| None` | `None` | Comma-separated provider IDs. `None` warms all providers. |

**Side Effects:** Starts specified provider processes.

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `warmed` | `list[str]` | Successfully warmed provider IDs |
| `already_warm` | `list[str]` | Providers that were already running |
| `failed` | `list[object]` | Failed providers with `id` and `error` |
| `summary` | `str` | Human-readable summary |

**Example:**

```json
// Request
{"providers": "math,filesystem"}

// Response
{
  "warmed": ["math"], "already_warm": ["filesystem"], "failed": [],
  "summary": "1 warmed, 1 already warm, 0 failed"
}
```

## Health

### `hangar_health` {#hangar_health}

System-wide health summary with provider state counts and security information.

**Parameters:** None.

**Side Effects:** None (read-only).

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `status` | `str` | Overall system status |
| `providers` | `object` | `total` and `by_state` breakdown (`cold`, `ready`, `degraded`, `dead`) |
| `groups` | `object` | `total`, `by_state`, `total_members`, `healthy_members` |
| `security` | `object` | Rate limiting info: `rate_limiting.active_buckets`, `rate_limiting.config` |

**Example:**

```json
// Request
{}

// Response
{
  "status": "healthy",
  "providers": {"total": 3, "by_state": {"ready": 2, "cold": 1}},
  "groups": {"total": 1, "by_state": {"healthy": 1}, "total_members": 3, "healthy_members": 3},
  "security": {"rate_limiting": {"active_buckets": 0, "config": {"rps": 10, "burst": 20}}}
}
```

### `hangar_metrics` {#hangar_metrics}

Provider metrics in JSON or Prometheus exposition format.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `format` | `str` | `"json"` | Output format: `"json"` or `"prometheus"` |

**Side Effects:** None (read-only).

**Returns (JSON format):**

| Field | Type | Description |
|-------|------|-------------|
| `providers` | `dict[str, object]` | Per-provider metrics: `state`, `mode`, `tools_count`, `invocations`, `errors`, `avg_latency_ms` |
| `groups` | `dict[str, object]` | Per-group metrics: `state`, `strategy`, `total_members`, `healthy_members` |
| `tool_calls` | `dict[str, object]` | Per-tool metrics keyed by `provider.tool`: `count`, `errors` |
| `discovery` | `object` | Discovery metrics |
| `errors` | `dict[str, int]` | Error counts by type |
| `performance` | `object` | Performance metrics |
| `summary` | `object` | Totals: `total_providers`, `total_groups`, `total_tool_calls`, `total_errors` |

When `format` is `"prometheus"`, the response contains a single `metrics` field with Prometheus exposition text.

**Example:**

```json
// Request
{"format": "json"}

// Response
{
  "providers": {"math": {"state": "ready", "mode": "subprocess", "tools_count": 4,
                          "invocations": 150, "errors": 2, "avg_latency_ms": 12.3}},
  "groups": {},
  "tool_calls": {"math.add": {"count": 100, "errors": 0}},
  "summary": {"total_providers": 1, "total_groups": 0, "total_tool_calls": 150, "total_errors": 2}
}
```

## Discovery

### `hangar_discover` (async) {#hangar_discover}

Trigger a discovery scan across all enabled sources. Discovered providers are added to the pending list for approval (unless `auto_register` is enabled).

**Parameters:** None.

**Side Effects:** Scans all enabled discovery sources. Updates the pending provider list.

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `discovered_count` | `int` | Total providers discovered |
| `registered_count` | `int` | Providers auto-registered |
| `updated_count` | `int` | Existing providers updated |
| `deregistered_count` | `int` | Providers deregistered (authoritative mode) |
| `quarantined_count` | `int` | Providers quarantined |
| `error_count` | `int` | Discovery errors |
| `duration_ms` | `float` | Scan duration in milliseconds |
| `source_results` | `dict[str, int]` | Discovered count per source type |

Returns `{error: "Discovery not configured. Enable discovery in config.yaml"}` when discovery is not enabled.

**Example:**

```json
// Request
{}

// Response
{
  "discovered_count": 5, "registered_count": 3, "updated_count": 1,
  "deregistered_count": 0, "quarantined_count": 1, "error_count": 0,
  "duration_ms": 1250.0, "source_results": {"docker": 3, "filesystem": 2}
}
```

### `hangar_discovered` {#hangar_discovered}

List providers discovered but not yet registered (pending approval).

**Parameters:** None.

**Side Effects:** None (read-only).

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `pending` | `list[object]` | Pending providers with `name`, `source`, `mode`, `discovered_at`, `fingerprint` |

Returns `{error: ...}` when discovery is not configured.

**Example:**

```json
// Request
{}

// Response
{
  "pending": [
    {"name": "new-api", "source": "docker", "mode": "remote",
     "discovered_at": "2026-01-15T10:30:00Z", "fingerprint": "abc123"}
  ]
}
```

### `hangar_quarantine` {#hangar_quarantine}

List providers that failed health checks during discovery and were quarantined.

**Parameters:** None.

**Side Effects:** None (read-only).

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `quarantined` | `list[object]` | Quarantined providers with `name`, `source`, `reason`, `quarantine_time` |

Returns `{error: ...}` when discovery is not configured.

**Example:**

```json
// Request
{}

// Response
{
  "quarantined": [
    {"name": "broken-api", "source": "docker", "reason": "health_check_failed",
     "quarantine_time": "2026-01-15T10:30:00Z"}
  ]
}
```

### `hangar_approve` (async) {#hangar_approve}

Approve a pending or quarantined provider for registration.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `provider` | `str` | required | Provider name from `hangar_discovered` or `hangar_quarantine` output |

**Side Effects:** Registers the provider in COLD state. Removes from pending or quarantine list.

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `approved` | `bool` | Whether approval succeeded |
| `provider` | `str` | Provider name |
| `status` | `str` | `"registered"` on success |
| `error` | `str` | Error message on failure |

Returns `{error: ...}` when discovery is not configured.

**Example:**

```json
// Request
{"provider": "new-api"}

// Response
{"approved": true, "provider": "new-api", "status": "registered"}
```

### `hangar_sources` (async) {#hangar_sources}

List the status of all configured discovery sources.

**Parameters:** None.

**Side Effects:** None (read-only).

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `sources` | `list[object]` | Sources with `source_type`, `mode`, `is_healthy`, `is_enabled`, `last_discovery`, `providers_count`, `error_message` |

Returns `{error: ...}` when discovery is not configured.

**Example:**

```json
// Request
{}

// Response
{
  "sources": [
    {"source_type": "docker", "mode": "additive", "is_healthy": true,
     "is_enabled": true, "last_discovery": "2026-01-15T10:30:00Z",
     "providers_count": 3, "error_message": null}
  ]
}
```

## Groups

### `hangar_group_list` {#hangar_group_list}

List all provider groups with member details, health state, and load balancing configuration.

**Parameters:** None.

**Side Effects:** None (read-only).

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `groups` | `list[object]` | Groups with `group_id`, `description`, `state`, `strategy`, `min_healthy`, `healthy_count`, `total_members`, `is_available`, `circuit_open`, `members` |

Each member in the `members` list contains: `id`, `state`, `in_rotation`, `weight`, `priority`, `consecutive_failures`.

**Example:**

```json
// Request
{}

// Response
{
  "groups": [
    {
      "group_id": "llm-group", "description": "LLM pool", "state": "healthy",
      "strategy": "round_robin", "min_healthy": 1, "healthy_count": 2,
      "total_members": 2, "is_available": true, "circuit_open": false,
      "members": [
        {"id": "llm-1", "state": "ready", "in_rotation": true, "weight": 50,
         "priority": 1, "consecutive_failures": 0},
        {"id": "llm-2", "state": "ready", "in_rotation": true, "weight": 50,
         "priority": 2, "consecutive_failures": 0}
      ]
    }
  ]
}
```

### `hangar_group_rebalance` {#hangar_group_rebalance}

Rebalance a group by re-checking all members. Recovered members rejoin rotation, failed members are removed. Resets the circuit breaker.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `group` | `str` | required | Group ID |

**Side Effects:** Re-checks all members. Updates rotation membership. Resets circuit breaker.

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `group_id` | `str` | Group ID |
| `state` | `str` | Group state after rebalance |
| `healthy_count` | `int` | Healthy member count |
| `total_members` | `int` | Total member count |
| `members_in_rotation` | `list[str]` | Member IDs currently in rotation |

**Example:**

```json
// Request
{"group": "llm-group"}

// Response
{
  "group_id": "llm-group", "state": "healthy", "healthy_count": 2,
  "total_members": 2, "members_in_rotation": ["llm-1", "llm-2"]
}
```

Errors: `ValueError("unknown_group: <id>")`

## Batch and Continuation

### `hangar_call` {#hangar_call}

Invoke tools on MCP providers. Supports single calls and parallel batch execution with two-level concurrency control (per-batch and system-wide).

**Parameters:**

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `calls` | `list[object]` | required | 1--100 items | List of `{provider, tool, arguments, timeout?}` objects |
| `max_concurrency` | `int` | `10` | 1--50 | Parallel workers for this batch |
| `timeout` | `float` | `60` | 1--300 | Batch timeout in seconds |
| `fail_fast` | `bool` | `false` | -- | Stop batch on first error |
| `max_attempts` | `int` | `1` | 1--10 | Total attempts per call including retries |

**Side Effects:** May start cold providers. Executes tool calls on providers.

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `batch_id` | `str` | Unique batch identifier |
| `success` | `bool` | `true` if all calls succeeded |
| `total` | `int` | Total calls in batch |
| `succeeded` | `int` | Successful call count |
| `failed` | `int` | Failed call count |
| `elapsed_ms` | `float` | Total batch execution time |
| `results` | `list[object]` | Per-call results with `index`, `call_id`, `success`, `result`, `error`, `error_type`, `elapsed_ms` |

On validation failure, the response contains `validation_errors` (list of `{index, field, message}`) instead of `results`. Individual results may include `truncated: true` with a `continuation_id` for large responses -- use `hangar_fetch_continuation` to retrieve the full data.

Results with retries include `retry_metadata` with `attempts` and `retries` counts.

**Example:**

```json
// Request
{
  "calls": [
    {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
    {"provider": "math", "tool": "multiply", "arguments": {"a": 3, "b": 4}}
  ],
  "max_concurrency": 5
}

// Response
{
  "batch_id": "batch-abc123", "success": true, "total": 2,
  "succeeded": 2, "failed": 0, "elapsed_ms": 45.2,
  "results": [
    {"index": 0, "call_id": "call-1", "success": true, "result": 3,
     "error": null, "error_type": null, "elapsed_ms": 20.1},
    {"index": 1, "call_id": "call-2", "success": true, "result": 12,
     "error": null, "error_type": null, "elapsed_ms": 22.8}
  ]
}
```

### `hangar_fetch_continuation` {#hangar_fetch_continuation}

Fetch full data for a truncated batch response. Continuation IDs are returned when individual call results exceed the response size limit.

**Parameters:**

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `continuation_id` | `str` | required | starts with `"cont_"` | Continuation ID from a truncated result |
| `offset` | `int` | `0` | >= 0 | Byte offset to start reading from |
| `limit` | `int` | `500000` | 1--2000000 | Maximum bytes to return |

**Side Effects:** None (read-only cache access).

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `found` | `bool` | Whether the continuation data exists |
| `data` | `any` | The continuation data (when found) |
| `total_size_bytes` | `int` | Total size of the cached data |
| `offset` | `int` | Current read offset |
| `has_more` | `bool` | Whether more data is available |
| `complete` | `bool` | Whether all data has been returned |

Returns `{found: false, error: "Continuation not found (may have expired)"}` when the continuation ID is invalid or expired.

**Example:**

```json
// Request
{"continuation_id": "cont_abc123", "offset": 0, "limit": 500000}

// Response
{
  "found": true, "data": "...full response content...",
  "total_size_bytes": 250000, "offset": 0, "has_more": false, "complete": true
}
```

Errors: `ValueError` if `continuation_id` is empty, does not start with `"cont_"`, or `offset` is negative.

### `hangar_delete_continuation` {#hangar_delete_continuation}

Delete cached continuation data to free memory.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `continuation_id` | `str` | required | Continuation ID to delete |

**Side Effects:** Removes the cached response from memory.

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `deleted` | `bool` | Whether the data was found and deleted |
| `continuation_id` | `str` | The requested continuation ID |

Returns `{deleted: false, continuation_id: "..."}` when the ID is not found. Returns `{deleted: false, continuation_id: "...", error: "..."}` when the truncation cache is unavailable.

**Example:**

```json
// Request
{"continuation_id": "cont_abc123"}

// Response
{"deleted": true, "continuation_id": "cont_abc123"}
```

Errors: `ValueError` if `continuation_id` is empty.
