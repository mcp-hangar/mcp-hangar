# Registry Tools API Reference

This document describes the MCP tools exposed by MCP Hangar.

## Tools Overview

| Tool | Description |
|------|-------------|
| `registry_list` | List all providers with status |
| `registry_start` | Start a provider |
| `registry_stop` | Stop a provider |
| `registry_tools` | Get tool schemas for a provider |
| `registry_invoke` | Invoke a tool on a provider |
| `registry_details` | Get detailed provider information |
| `registry_health` | Get registry health status |

## registry_list

List all registered providers with their current status.

**Parameters**:

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `state_filter` | string | No | `null` | Filter by state: `cold`, `ready`, `degraded`, `dead` |

**Response**:

```json
{
  "providers": [
    {
      "provider_id": "math_subprocess",
      "state": "ready",
      "mode": "subprocess",
      "is_alive": true,
      "tools_count": 5,
      "health_status": "healthy"
    }
  ]
}
```

**Example**:

```python
result = registry_list()
result = registry_list(state_filter="ready")
```

## registry_start

Explicitly start a provider and discover its tools.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `provider` | string | Yes | Provider ID to start |

**Response**:

```json
{
  "provider": "math_subprocess",
  "state": "ready",
  "tools": ["add", "subtract", "multiply", "divide"]
}
```

**Errors**:

| Error | Description |
|-------|-------------|
| `ValueError: unknown_provider` | Provider ID not found |
| `ProviderStartError` | Provider failed to start |
| `ProviderDegradedError` | Provider is degraded, backoff in progress |

## registry_stop

Stop a running provider.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `provider` | string | Yes | Provider ID to stop |

**Response**:

```json
{
  "stopped": "math_subprocess",
  "reason": "shutdown"
}
```

Stopping a provider gracefully closes its process. The provider can be restarted later with `registry_start` or auto-started on next `registry_invoke`.

## registry_tools

Get detailed tool schemas for a provider.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `provider` | string | Yes | Provider ID |

**Response**:

```json
{
  "provider": "math_subprocess",
  "tools": [
    {
      "name": "add",
      "description": "Add two numbers",
      "inputSchema": {
        "type": "object",
        "properties": {
          "a": {"type": "number"},
          "b": {"type": "number"}
        },
        "required": ["a", "b"]
      }
    }
  ]
}
```

This will auto-start the provider if not already running. Tool schemas are cached after initial discovery.

## registry_invoke

Invoke a tool on a provider.

**Parameters**:

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `provider` | string | Yes | - | Provider ID |
| `tool` | string | Yes | - | Tool name to invoke |
| `arguments` | object | Yes | - | Tool arguments |
| `timeout` | float | No | `30.0` | Timeout in seconds |

**Response**: Depends on the tool being invoked.

```json
{"result": 8}
```

**Errors**:

| Error | Description |
|-------|-------------|
| `ValueError: unknown_provider` | Provider ID not found |
| `ToolNotFoundError` | Tool doesn't exist on provider |
| `ToolInvocationError` | Tool execution failed |
| `TimeoutError` | Invocation timed out |
| `ProviderDegradedError` | Provider is degraded |

**Example**:

```python
result = registry_invoke(
    provider="math_subprocess",
    tool="add",
    arguments={"a": 5, "b": 3}
)
print(result["result"])  # 8

result = registry_invoke(
    provider="long_running",
    tool="process",
    arguments={"data": "..."},
    timeout=120.0
)
```

Provider will be auto-started if not running. Maximum argument size is 1MB.

## registry_details

Get detailed information about a specific provider.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `provider` | string | Yes | Provider ID |

**Response**:

```json
{
  "provider_id": "math_subprocess",
  "state": "ready",
  "mode": "subprocess",
  "is_alive": true,
  "tools": [...],
  "health": {
    "consecutive_failures": 0,
    "last_success_at": 1704067200.0,
    "last_failure_at": null,
    "total_invocations": 42,
    "total_failures": 1,
    "success_rate": 0.976,
    "can_retry": true,
    "time_until_retry": 0.0
  },
  "idle_time": 15.5,
  "meta": {
    "tools_count": 5,
    "started_at": 1704067185.0
  }
}
```

## registry_health

Get registry health status.

**Response**:

```json
{
  "status": "healthy",
  "providers": {
    "total": 3,
    "ready": 2,
    "degraded": 0,
    "cold": 1
  }
}
```

## Error Handling

All tools return structured errors:

```json
{
  "error": "error message",
  "provider_id": "math_subprocess",
  "operation": "invoke",
  "details": {
    "tool_name": "divide",
    "correlation_id": "abc-123"
  },
  "type": "ToolInvocationError"
}
```

### Error Types

| Type | Description |
|------|-------------|
| `ProviderNotFoundError` | Provider ID not in configuration |
| `ProviderStartError` | Failed to start provider |
| `ProviderDegradedError` | Provider is degraded (circuit breaker) |
| `ToolNotFoundError` | Tool doesn't exist |
| `ToolInvocationError` | Tool execution failed |
| `ToolTimeoutError` | Tool execution timed out |
| `ValidationError` | Invalid input parameters |
| `RateLimitExceeded` | Rate limit violated |

## Provider States

| State | Description | Allowed Operations |
|-------|-------------|-------------------|
| `cold` | Not started | `start`, `invoke` (auto-starts) |
| `initializing` | Starting up | Wait |
| `ready` | Running and healthy | All operations |
| `degraded` | Circuit breaker active | Wait for backoff |
| `dead` | Process died | `start`, `invoke` (auto-restarts) |

## Configuration

Providers are configured in `config.yaml`:

```yaml
providers:
  math_subprocess:
    mode: subprocess
    command:
      - python
      - -m
      - examples.provider_math.server
    idle_ttl_s: 180
    health_check_interval_s: 60
    max_consecutive_failures: 3

  math_docker:
    mode: container
    image: mcp-math:latest
    idle_ttl_s: 300
    env:
      API_KEY: "${API_KEY}"
```

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `mode` | string | Required | `subprocess`, `docker`, `container`, `podman` |
| `command` | list | - | Command for subprocess mode |
| `image` | string | - | Image for container mode |
| `env` | object | `{}` | Environment variables |
| `idle_ttl_s` | integer | `300` | Seconds before idle shutdown |
| `health_check_interval_s` | integer | `60` | Health check frequency |
| `max_consecutive_failures` | integer | `3` | Failures before degradation |
