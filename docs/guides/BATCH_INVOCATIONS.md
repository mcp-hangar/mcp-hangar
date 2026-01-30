# Tool Invocations with hangar_call

Execute one or more tool invocations with a single unified API.

## Overview

The `hangar_call()` tool is the unified API for all tool invocations. Whether you need a single call or parallel batch execution, the format is consistent.

**Key benefits:**
- **Unified API** - One function for single calls and batches
- **Parallel execution** - Multiple calls run concurrently
- **Automatic retry** - Built-in retry with exponential backoff
- **Single-flight cold starts** - Multiple calls to the same COLD provider trigger only one startup
- **Partial success handling** - Failed calls don't block successful ones
- **Fail-fast mode** - Optionally abort on first error
- **Circuit breaker integration** - Respects provider health status

## Basic Usage

### Single Invocation

```python
# Simple call
hangar_call(calls=[
    {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}}
])

# With retry for reliability
hangar_call(
    calls=[{"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}}],
    max_retries=3
)
```

### Batch Invocation (Parallel)

```python
# Execute multiple calls in parallel - much faster than sequential
hangar_call(calls=[
    {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
    {"provider": "math", "tool": "multiply", "arguments": {"a": 3, "b": 4}},
    {"provider": "fetch", "tool": "get", "arguments": {"url": "https://api.example.com"}},
])
# Total time: max(t1, t2, t3) instead of t1 + t2 + t3
```

## API Reference

### hangar_call

```python
hangar_call(
    calls: list[dict],           # List of invocations to execute
    max_concurrency: int = 10,   # Max parallel invocations (1-20)
    timeout: float = 60.0,       # Global timeout in seconds (1-300)
    fail_fast: bool = False,     # Abort on first error
    max_retries: int = 1,        # Retry attempts per call (1-10)
) -> dict
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `calls` | `list[dict]` | required | List of call specifications |
| `max_concurrency` | `int` | 10 | Maximum parallel workers (1-20) |
| `timeout` | `float` | 60.0 | Global timeout for entire batch (1-300s) |
| `fail_fast` | `bool` | False | If True, abort remaining calls on first error |
| `max_retries` | `int` | 1 | Retry attempts per call (1-10, default 1 = no retry) |

**Call specification:**

Each item in `calls` must be a dictionary with:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider` | `str` | Yes | Provider ID |
| `tool` | `str` | Yes | Tool name |
| `arguments` | `dict` | Yes | Tool arguments |
| `timeout` | `float` | No | Per-call timeout (overrides global) |

### Response Schema

```python
{
    "batch_id": "550e8400-e29b-41d4-a716-446655440000",  # UUID for tracing
    "success": True,          # True if ALL calls succeeded
    "total": 3,               # Total number of calls
    "succeeded": 3,           # Number of successful calls
    "failed": 0,              # Number of failed calls
    "elapsed_ms": 1234.5,     # Total batch execution time
    "results": [
        {
            "index": 0,                      # Original position in calls array
            "call_id": "...",                # UUID for per-call tracing
            "success": True,
            "result": {"sum": 3},            # Tool result if success
            "error": None,                   # Error message if failed
            "error_type": None,              # Error classification
            "elapsed_ms": 45.2,              # Individual call duration
            "retry_metadata": {              # Present if max_retries > 1
                "attempts": 2,
                "retries": ["TimeoutError"],
                "total_time_ms": 1234.5
            }
        },
        # ... more results
    ]
}
```

## Examples

### With Automatic Retry

```python
# Retry on transient failures (recommended for unreliable providers)
hangar_call(
    calls=[
        {"provider": "fetch", "tool": "get", "arguments": {"url": "https://api.example.com"}}
    ],
    max_retries=3
)

# Response includes retry metadata:
{
    "results": [{
        "success": True,
        "result": {...},
        "retry_metadata": {
            "attempts": 2,           # Succeeded on 2nd attempt
            "retries": ["TimeoutError"],  # 1st attempt failed with timeout
            "total_time_ms": 1500.0
        }
    }]
}
```

### Mixed Providers (Parallel Cold Starts)

When calling multiple COLD providers, they start in parallel:

```python
hangar_call(calls=[
    {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
    {"provider": "sqlite", "tool": "query", "arguments": {"sql": "SELECT 1"}},
    {"provider": "fetch", "tool": "get", "arguments": {"url": "https://api.github.com"}},
], max_concurrency=3)
# All 3 providers start simultaneously if COLD
```

### Fail-Fast Mode

Stop processing on first error:

```python
results = hangar_call(
    calls=[
        {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
        {"provider": "nonexistent", "tool": "foo", "arguments": {}},  # Will fail
        {"provider": "math", "tool": "multiply", "arguments": {"a": 3, "b": 4}},
    ],
    fail_fast=True,
)
# If call #1 fails, call #2 won't execute
```

### Per-Call Timeouts

Different timeouts for different calls:

```python
hangar_call(calls=[
    {"provider": "fetch", "tool": "get", "arguments": {"url": "..."}, "timeout": 5.0},
    {"provider": "ml", "tool": "predict", "arguments": {...}, "timeout": 30.0},
], timeout=60.0)
# Effective timeout = min(per_call_timeout, remaining_global_timeout)
```

### Circuit Breaker Behavior

If a provider's circuit breaker is OPEN, calls to it fail immediately:

```python
results = hangar_call(calls=[
    {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
    {"provider": "unhealthy_provider", "tool": "foo", "arguments": {}},  # CB OPEN
])
# Response:
{
    "success": False,  # Partial failure
    "succeeded": 1,
    "failed": 1,
    "results": [
        {"index": 0, "success": True, "result": {"sum": 3}, ...},
        {"index": 1, "success": False, "error": "Circuit breaker open", "error_type": "CircuitBreakerOpen", ...}
    ]
}
```

## Behavior Details

### Validation

Batch validation is **eager** - the entire batch is validated before any execution:

- Provider existence
- Tool existence (for providers with predefined tools)
- Argument types
- Batch size limits
- Timeout bounds

If validation fails, no calls are executed:

```python
{
    "success": False,
    "error": "Validation failed",
    "validation_errors": [
        {"index": 0, "field": "provider", "message": "Provider 'foo' not found"}
    ]
}
```

### Single-Flight Cold Starts

When multiple calls target the same COLD provider, the provider starts exactly once:

```python
# 5 calls to COLD "math" provider = 1 startup, then 5 parallel tool calls
hangar_call(calls=[
    {"provider": "math", "tool": "add", "arguments": {"a": i, "b": 1}}
    for i in range(5)
])
```

### Retry Behavior

When `max_retries > 1`:

- Retries use exponential backoff
- Only transient errors trigger retry (timeout, network errors, malformed JSON)
- Permanent errors (validation, provider not found) do not retry
- Each call retries independently within the batch

### Timeout Resolution

Effective timeout per call = `min(per_call_timeout, remaining_global_timeout)`

Example:
- Global timeout: 60s
- Per-call timeout: 30s
- Elapsed time: 50s
- Effective timeout: min(30, 10) = 10s

### Response Truncation

Large responses are truncated to prevent memory issues:

- Max response per call: 10MB
- Max total batch response: 50MB

Truncated responses have `truncated: true` flag:

```python
{
    "index": 2,
    "success": True,
    "truncated": True,
    "truncated_reason": "response_size_exceeded",
    "original_size_bytes": 15728640,
    "result": None  # No partial data
}
```

## Limits

| Limit | Value | Behavior |
|-------|-------|----------|
| Max calls per batch | 100 | Validation error |
| Max concurrency | 20 | Clamped to limit |
| Max timeout | 300s | Clamped to limit |
| Max retries | 10 | Clamped to limit |
| Max response per call | 10MB | Truncated |
| Max total response | 50MB | Truncated |

## Metrics

Prometheus metrics for batch operations:

```
mcp_hangar_batch_calls_total{result="success|partial|failure|validation_error"}
mcp_hangar_batch_size_histogram{}
mcp_hangar_batch_duration_seconds{}
mcp_hangar_batch_concurrency_gauge{}
mcp_hangar_batch_truncations_total{reason="per_call|total_size"}
mcp_hangar_batch_circuit_breaker_rejections_total{provider="..."}
mcp_hangar_batch_cancellations_total{reason="timeout|fail_fast"}
```

## Configuration

Optional configuration in `config.yaml`:

```yaml
batch:
  max_calls: 100
  max_concurrency: 20
  default_timeout: 60.0
  max_timeout: 300.0
  max_response_size_bytes: 10485760      # 10MB per call
  max_total_response_size_bytes: 52428800  # 50MB total
```

## Migration from Previous API

If you were using the previous tools, here's how to migrate:

| Old API | New API |
|---------|---------|
| `registry_invoke(provider, tool, arguments)` | `hangar_call(calls=[{"provider": ..., "tool": ..., "arguments": ...}])` |
| `registry_invoke_ex(..., max_retries=5)` | `hangar_call(calls=[...], max_retries=5)` |
| `registry_invoke_stream(...)` | `hangar_call(calls=[...])` (progress logged internally) |
| `hangar_batch(calls=[...])` | `hangar_call(calls=[...])` |

## Best Practices

1. **Use retry for external calls** - Set `max_retries=3` for fetch, database, and network operations
2. **Group related calls** - Batch calls that can run independently
3. **Set appropriate timeouts** - Use per-call timeouts for varying workloads
4. **Monitor metrics** - Watch `batch_duration_seconds` and `batch_size_histogram`
5. **Handle partial failures** - Check `succeeded` and `failed` counts
6. **Use fail-fast sparingly** - Only when all-or-nothing is required

## Limitations

- **No dependency ordering** - Calls are independent; use sequential calls if you need result of A as input to B
- **No streaming** - Results are returned when all calls complete
