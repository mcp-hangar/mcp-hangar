# Hot-Reload Configuration

Reload configuration without restarting the server. Add, remove, or modify providers while preserving active connections for unchanged providers.

## Quick Start

```bash
# Start server
mcp-hangar serve --http --port 8000

# Edit config.yaml in another terminal - changes apply automatically

# Or trigger manually
kill -HUP $(pgrep -f "mcp-hangar serve")
```

## Overview

| Trigger | Latency | Use Case |
|---------|---------|----------|
| File watcher (watchdog) | ~1s | Development, real-time updates |
| File polling | 5s (configurable) | Environments without inotify/fsevents |
| SIGHUP signal | Immediate | Scripted deployments, CI/CD |
| MCP tool | Immediate | Interactive reload from AI assistant |

All reload operations are **atomic**: changes are validated before application. Invalid configuration is rejected; current config preserved.

## Configuration

```yaml
# Optional: customize hot-reload behavior
config_reload:
  enabled: true       # default: true
  use_watchdog: true  # default: true, falls back to polling
  interval_s: 5       # polling interval when watchdog unavailable
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | bool | `true` | Enable automatic file watching |
| `use_watchdog` | bool | `true` | Use watchdog library (inotify/fsevents) |
| `interval_s` | int | `5` | Polling interval in seconds |

## Triggering Reload

### Automatic File Watching

Enabled by default. Uses [watchdog](https://github.com/gorakhargosh/watchdog) for efficient file system events with polling fallback.

### SIGHUP Signal

Standard Unix reload pattern. Does not terminate the process.

```bash
# Find process
pgrep -f "mcp-hangar serve"

# Reload
kill -HUP <PID>

# One-liner
kill -HUP $(pgrep -f "mcp-hangar serve")
```

### MCP Tool

Reload from Claude Desktop or any MCP client:

```python
hangar_reload_config()                    # Graceful reload
hangar_reload_config(graceful=false)      # Immediate shutdown
```

**Response:**

```json
{
  "status": "success",
  "providers_added": ["new-api"],
  "providers_removed": ["deprecated-service"],
  "providers_updated": ["modified-provider"],
  "providers_unchanged": ["stable-provider"],
  "duration_ms": 45.2
}
```

## Reload Behavior

| Scenario | Behavior | Final State |
|----------|----------|-------------|
| **Added** | Registered but not started | `COLD` |
| **Removed** | Stopped gracefully, then removed | (deleted) |
| **Modified** | Old stopped, new registered | `COLD` |
| **Unchanged** | No action taken | Preserved |

### Compared Fields

Changes to any of these fields trigger provider restart:

| Field | Description |
|-------|-------------|
| `mode` | Provider mode (subprocess, docker, remote) |
| `command` | Command and arguments |
| `image` | Container image |
| `endpoint` | Remote endpoint URL |
| `env` | Environment variables |
| `volumes` | Volume mounts |
| `network` | Network mode |
| `user` | User/group ID |
| `idle_ttl_s` | Idle timeout |
| `health_check_interval_s` | Health check interval |
| `max_consecutive_failures` | Failure threshold |

**Normalization:**

- `{}` is equivalent to `null` for `env`, `resources`
- `[]` is equivalent to `null` for `volumes`, `command`
- Missing fields use default values

## Examples

### Add Provider

**Before:**

```yaml
providers:
  math:
    mode: subprocess
    command: [python, -m, math_server]
```

**After:**

```yaml
providers:
  math:
    mode: subprocess
    command: [python, -m, math_server]

  filesystem:
    mode: subprocess
    command: [npx, -y, "@modelcontextprotocol/server-filesystem"]
    args: ["/home/user/documents"]
```

**Result:** `math` unchanged, `filesystem` added in `COLD` state.

### Update Provider

**Before:**

```yaml
providers:
  database:
    mode: remote
    endpoint: https://db-mcp.internal/v1
    idle_ttl_s: 300
```

**After:**

```yaml
providers:
  database:
    mode: remote
    endpoint: https://db-mcp.internal/v2
    idle_ttl_s: 600
    env:
      POOL_SIZE: "10"
```

**Result:** `database` stopped gracefully, new instance created.

### Remove Provider

**Before:**

```yaml
providers:
  legacy-api:
    mode: subprocess
    command: [python, legacy_server.py]

  modern-api:
    mode: remote
    endpoint: https://api.example.com/mcp
```

**After:**

```yaml
providers:
  modern-api:
    mode: remote
    endpoint: https://api.example.com/mcp
```

**Result:** `legacy-api` stopped and removed, `modern-api` unchanged.

### Change Provider Mode

**Before:**

```yaml
providers:
  search:
    mode: subprocess
    command: [python, -m, search_v1]
```

**After:**

```yaml
providers:
  search:
    mode: docker
    image: search-mcp:v2
    env:
      INDEX_PATH: /data/index
    volumes:
      - ./data:/data:ro
```

**Result:** Subprocess stopped, Docker container started.

## Events

Hot-reload emits domain events for observability:

| Event | When |
|-------|------|
| `ConfigurationReloadRequested` | Before reload starts |
| `ConfigurationReloaded` | After successful reload |
| `ConfigurationReloadFailed` | On validation/apply failure |

```python
ConfigurationReloaded(
    config_path="/app/config.yaml",
    providers_added=["new-api"],
    providers_removed=["old-api"],
    providers_updated=["modified"],
    providers_unchanged=["stable"],
    reload_duration_ms=42.5,
    requested_by="file_watcher"
)
```

## Disabling Hot-Reload

```yaml
config_reload:
  enabled: false
```

> SIGHUP and MCP tool remain functional for manual reload.

## Monitoring

### Log Events

| Event | Description |
|-------|-------------|
| `config_reload_worker_started` | Worker initialized |
| `config_file_modified_detected` | File change detected |
| `triggering_config_reload` | Reload initiated |
| `configuration_reloaded` | Reload successful |
| `configuration_reload_failed` | Reload failed |

### Structured Logs

```json
{"event": "configuration_reloaded", "config_path": "config.yaml",
 "duration_ms": 42.5, "added": 1, "removed": 0, "updated": 2}
```

## Limitations

| Limitation | Workaround |
|------------|------------|
| Provider groups cleared on reload | Groups reconstructed from new config |
| Hot-loaded providers unaffected | Use `hangar_unload` to manage separately |
| Event store config requires restart | Restart server for event store changes |
| In-flight requests | Completed before provider stops (graceful mode) |

## Troubleshooting

### Reload Not Triggering

```bash
# Check watchdog installed
pip list | grep watchdog

# Check worker status
grep "config_reload_worker" logs/mcp-hangar.log

# Manual reload
kill -HUP $(pgrep -f "mcp-hangar serve")
```

### Invalid Configuration

```bash
# Validate YAML
python -c "import yaml; yaml.safe_load(open('config.yaml'))"

# Check providers section
grep -q "^providers:" config.yaml && echo "OK" || echo "Missing"

# Check logs
grep "configuration_reload_failed" logs/mcp-hangar.log
```

### Provider Not Starting

```bash
# Check provider added
grep "provider_added" logs/mcp-hangar.log

# Check status
hangar_status()

# Start manually
hangar_start(provider="my-provider")
```

## Security

| Consideration | Implementation |
|---------------|----------------|
| Validation before apply | Invalid config rejected |
| Atomic operations | All-or-nothing semantics |
| Graceful shutdown | Active requests complete first |
| Audit trail | All reloads logged with source |
| File permissions | Ensure config not world-writable |
