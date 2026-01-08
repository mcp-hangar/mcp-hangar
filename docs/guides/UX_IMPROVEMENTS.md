# MCP-Hangar UX Improvements

This document describes the UX improvements implemented in mcp-hangar to make the system "just work" without requiring users to understand provider lifecycle or write retry logic.

## Overview

Three main improvements were implemented:

1. **Human-Readable Errors** - Rich error messages with recovery hints
2. **Automatic Retry** - Built-in retry with exponential backoff
3. **Real-Time Progress** - Streaming progress updates for long operations

Plus bonus features:

4. **Status Dashboard** - `registry_status()` for quick overview
5. **Provider Warming** - `registry_warm()` to pre-start providers

---

## 1. Human-Readable Errors

### Problem

```
Error: "Expecting value: line 1 column 1 (char 0)"
```

User reaction: "WTF does this mean?"

### Solution

Rich error classes with:
- Clear user-facing message
- Technical details for debugging
- Actionable recovery hints
- Related log references

### Usage

```python
from mcp_hangar import (
    HangarError,
    ProviderProtocolError,
    ProviderCrashError,
    NetworkError,
    map_exception_to_hangar_error,
)

# Errors are automatically raised with full context
try:
    registry_invoke("sqlite", "query", {"sql": "SELECT"})
except HangarError as e:
    print(e)
    # Output:
    # ProviderProtocolError: SQLite provider returned invalid response
    #   ↳ Provider: sqlite
    #   ↳ Operation: query
    #   ↳ Details: Expected JSON object, received plain text
    #
    # 💡 Recovery steps:
    #   1. Retry the operation (often transient)
    #   2. Check provider logs: registry_details('sqlite')
    #   3. If persistent, file bug report
```

### Error Types

| Error Class | Use Case | Retryable |
|------------|----------|-----------|
| `TransientError` | Temporary failures | ✅ |
| `ProviderProtocolError` | Invalid JSON response | ✅ |
| `ProviderCrashError` | Provider process died | ✅ |
| `NetworkError` | Connection issues | ✅ |
| `TimeoutError` | Operation timed out | ✅ |
| `ConfigurationError` | Config problems | ❌ |
| `ProviderNotFoundError` | Unknown provider | ❌ |
| `ToolNotFoundError` | Unknown tool | ❌ |
| `RateLimitError` | Too many requests | ❌ |

### Mapping Exceptions

```python
from mcp_hangar import map_exception_to_hangar_error

try:
    risky_operation()
except Exception as e:
    hangar_error = map_exception_to_hangar_error(
        e,
        provider="my-provider",
        operation="my-operation",
    )
    # hangar_error is now a rich HangarError with recovery hints
```

---

## 2. Automatic Retry

### Problem

Transient failures require manual retry logic at every call site.

### Solution

Built-in retry with exponential backoff, configurable per-provider.
Error classification automatically distinguishes transient vs permanent errors.

### Usage

#### Option 1: Use `registry_invoke_ex` (Recommended)

```python
# Extended invoke with automatic retry
result = registry_invoke_ex(
    provider="sqlite",
    tool="query",
    arguments={"sql": "SELECT * FROM users"},
    timeout=30.0,
    max_retries=3,        # Retry up to 3 times
    retry_on_error=True,  # Enable retry for transient errors
    correlation_id="my-trace-001",  # Optional tracing ID
)

# Success result includes retry metadata
print(result["_retry_metadata"])
# {
#     "correlation_id": "my-trace-001",
#     "attempts": 2,
#     "total_time_ms": 1234.5,
#     "retries": ["ProviderProtocolError"]
# }

# Error result includes enriched metadata
error_result = registry_invoke_ex(
    provider="math",
    tool="divide",
    arguments={"a": 10, "b": 0},  # Division by zero!
)
print(error_result["_retry_metadata"])
# {
#     "correlation_id": "auto-uuid-here",
#     "attempts": 1,
#     "total_time_ms": 2.5,
#     "retries": [],
#     "final_error_reason": "permanent: validation_error",
#     "recovery_hints": ["Check arguments: divisor cannot be zero"]
# }
```

#### Option 2: Manual Retry Control

```python
from mcp_hangar import RetryPolicy, BackoffStrategy, retry_sync

policy = RetryPolicy(
    max_attempts=5,
    backoff=BackoffStrategy.EXPONENTIAL,
    initial_delay=1.0,
    max_delay=30.0,
    retry_on=["Timeout", "MalformedJSON"],
)

result = retry_sync(
    operation=lambda: risky_call(),
    policy=policy,
    provider="my-provider",
    operation_name="my-operation",
)

if result.success:
    print(result.result)
else:
    raise result.final_error
```

#### Option 3: Decorator

```python
from mcp_hangar import with_retry, RetryPolicy

@with_retry(RetryPolicy(max_attempts=3))
async def call_provider():
    return await client.invoke("tool", {})
```

### Configuration (config.yaml)

```yaml
retry:
  default_policy:
    max_attempts: 3
    backoff: exponential  # exponential, linear, constant
    initial_delay: 1.0
    max_delay: 30.0
    retry_on:
      - MalformedJSON
      - Timeout
      - ConnectionError
      - TransientError
      - ProviderProtocolError
      - NetworkError

  per_provider:
    sqlite:
      max_attempts: 5  # Database queries need more retries
    fetch:
      max_attempts: 2  # Network fails fast
      initial_delay: 0.5
```

### Backoff Strategies

| Strategy | Formula | Use Case |
|----------|---------|----------|
| `exponential` | `min(initial * 2^attempt, max)` | Most cases |
| `linear` | `min(initial * (attempt+1), max)` | Predictable delays |
| `constant` | `initial` | Quick retries |

---

## 3. Real-Time Progress

### Problem

User calls `registry_invoke()` → waits in silence → gets result or error.
No visibility into cold starts, container initialization, etc.

### Solution

Progress is available in multiple ways:

1. **`registry_invoke_stream`** - Real-time MCP notifications (for models!)
2. **`registry_invoke_ex`** - Progress in `_progress` response field
3. **Logs** - Progress logged with `operation_progress` event

### Option 1: Real-Time Progress for Models (`registry_invoke_stream`) ⭐

Use `registry_invoke_stream` when you want the model to see progress **in real-time**:

```python
# Model calls this and receives progress notifications during execution
result = registry_invoke_stream(
    provider="sqlite",
    tool="query",
    arguments={"sql": "SELECT * FROM large_table"},
    correlation_id="trace-001"  # Optional: for distributed tracing
)

# During execution, model receives MCP progress notifications:
# [1/5] [cold_start] Provider is cold, launching...
# [2/5] [launching] Starting container provider...
# [3/5] [ready] Provider ready
# [4/5] [executing] Calling tool 'query'...
# [5/5] [complete] Operation completed in 1234ms

# Response includes full progress history and correlation_id:
print(result["_retry_metadata"]["correlation_id"])  # "trace-001"
print(result["_progress"])  # Full list of progress events
```

This uses MCP's built-in `notifications/progress` mechanism.
**The model sees progress updates while waiting for the result!**

### Option 2: Progress in Response (`registry_invoke_ex`)

Progress events are included in the response after completion:

```python
result = registry_invoke_ex("math", "add", {"a": 1, "b": 2}, correlation_id="my-trace")

# Result includes progress events and correlation_id
print(result["_retry_metadata"]["correlation_id"])  # "my-trace" or auto-generated UUID
print(result["_progress"])
# [
#   {"stage": "ready", "message": "Starting operation...", "elapsed_ms": 0.01},
#   {"stage": "executing", "message": "Calling tool 'add'...", "elapsed_ms": 0.2},
#   {"stage": "processing", "message": "Processing response...", "elapsed_ms": 1.5},
#   {"stage": "complete", "message": "Operation completed", "elapsed_ms": 1.7}
# ]

# For cold providers, you'll also see:
# [
#   {"stage": "cold_start", "message": "Provider is cold, launching...", "elapsed_ms": 0.0},
#   {"stage": "launching", "message": "Starting subprocess provider...", "elapsed_ms": 15.2},
#   {"stage": "ready", "message": "Provider ready", "elapsed_ms": 500.0},
#   ...
# ]
```

### Error Responses with Enriched Metadata

When errors occur, the response includes classification and recovery hints:

```python
result = registry_invoke_ex("math", "divide", {"a": 10, "b": 0})

# Error response structure:
{
    "content": "Error executing tool divide: division by zero",
    "isError": True,
    "_retry_metadata": {
        "correlation_id": "uuid-here",
        "attempts": 1,
        "total_time_ms": 1.5,
        "retries": [],
        "final_error_reason": "permanent: validation_error",
        "recovery_hints": ["Check arguments: divisor cannot be zero"]
    },
    "_progress": [...]
}
```

### Option 3: Watching Logs

Progress is also logged in real-time with correlation_id:

```bash
# In another terminal, watch logs
tail -f logs/mcp-hangar.log | grep operation_progress
```

Log output:
```json
{"event": "operation_progress", "provider": "math", "tool": "add", "correlation_id": "uuid-here", "stage": "launching", "message": "Starting subprocess provider...", "elapsed_ms": 15.2}
{"event": "operation_progress", "provider": "math", "tool": "add", "correlation_id": "uuid-here", "stage": "executing", "message": "Calling tool 'add'...", "elapsed_ms": 234.1}
```

### Programmatic Usage (Python API)

For direct Python usage, you can provide a callback:

```python
from mcp_hangar.progress import ProgressTracker, ProgressStage, create_progress_tracker

def on_progress(stage: str, message: str, elapsed_ms: float):
    print(f"⏳ [{stage}] {message} ({elapsed_ms:.0f}ms)")

tracker = create_progress_tracker(
    provider="math",
    operation="add",
    callback=on_progress
)

tracker.report(ProgressStage.COLD_START, "Provider is cold...")
tracker.report(ProgressStage.LAUNCHING, "Starting container...")
tracker.report(ProgressStage.READY, "Provider ready!")
result = do_operation()
tracker.complete(result)
```

### Progress Stages

| Stage | Description |
|-------|-------------|
| `cold_start` | Provider needs to start |
| `launching` | Starting container/process |
| `initializing` | Container started, initializing |
| `discovering_tools` | Discovering available tools |
| `connecting` | Connecting to provider |
| `ready` | Provider ready |
| `executing` | Calling tool |
| `processing` | Processing response |
| `complete` | Operation finished |
| `failed` | Operation failed |
| `retrying` | Retry in progress |

---

## 4. Status Dashboard

### Usage

```python
status = registry_status()
print(status["formatted"])
```

### Output

```
╭─────────────────────────────────────────────────╮
│ MCP-Hangar Status                               │
├─────────────────────────────────────────────────┤
│ ✅ math         ready    last: 2s ago           │
│ ✅ sqlite       ready    last: 15s ago          │
│ ⏸️  fetch        cold     Will start on request │
│ 🔄 memory       starting                        │
│ ❌ filesystem   error                           │
├─────────────────────────────────────────────────┤
│ Health: 2/5 providers healthy                   │
│ Uptime: 8h 23m                                  │
╰─────────────────────────────────────────────────╯
```

### Status Indicators

| Icon | State | Meaning |
|------|-------|---------|
| ✅ | ready | Running and healthy |
| ⏸️ | cold | Will start on first request |
| 🔄 | starting | Starting up |
| ⚠️ | degraded | Has errors, in backoff |
| ❌ | dead/error | Failed |

---

## 5. Provider Warming

Pre-start providers to avoid cold start latency.

### Usage

```python
# Warm specific providers
result = registry_warm("math,sqlite")

# Warm all providers
result = registry_warm()

print(result)
# {
#     "warmed": ["math", "sqlite"],
#     "already_warm": [],
#     "failed": [],
#     "summary": "Warmed 2 providers, 0 already warm, 0 failed"
# }
```

---

## Success Metrics

| Metric | Before | After |
|--------|--------|-------|
| Time to understand error | 5 min | 10 sec |
| Manual retry code needed | Yes | No |
| Understanding lifecycle required | Yes | No |
| User sentiment | "WTF is happening?" | "OK, makes sense" |

---

## Migration Guide

### Existing Code

No changes required! All improvements are backward compatible.

### Recommended Updates

```python
# Before (still works)
result = registry_invoke("math", "add", {"a": 1, "b": 2})

# After (better UX with retry)
result = registry_invoke_ex(
    "math", "add", {"a": 1, "b": 2},
    max_retries=3,
)
```

### Error Handling

```python
from mcp_hangar import HangarError

try:
    result = registry_invoke_ex("provider", "tool", {})
except HangarError as e:
    # Rich error with context and hints
    print(e)
    print(e.recovery_hints)
```

---

## API Reference

### Error Classes

```python
from mcp_hangar import (
    HangarError,           # Base class
    TransientError,        # Retryable temporary failures
    ProviderProtocolError, # Invalid provider responses
    ProviderCrashError,    # Provider process died
    NetworkError,          # Connection issues
    HangarConfigurationError,  # Config problems
    HangarProviderNotFoundError,  # Unknown provider
    HangarToolNotFoundError,   # Unknown tool
    HangarTimeoutError,    # Operation timed out
    RateLimitError,        # Rate limit exceeded
    HangarProviderDegradedError,  # Provider in backoff
)
```

### Retry Classes

```python
from mcp_hangar import (
    RetryPolicy,       # Retry configuration
    BackoffStrategy,   # EXPONENTIAL, LINEAR, CONSTANT
    RetryResult,       # Result with attempt history
    get_retry_policy,  # Get policy for provider
    get_retry_store,   # Access config store
    with_retry,        # Decorator
)
```

### Progress Classes

```python
from mcp_hangar import (
    ProgressStage,          # Stage enum
    ProgressEvent,          # Event dataclass
    ProgressTracker,        # Track progress
    ProgressCallback,       # Callback type hint
    create_progress_tracker, # Factory function
    get_stage_message,      # Get message template
)
```

### Utility Functions

```python
from mcp_hangar import (
    map_exception_to_hangar_error,  # Convert exceptions
    is_retryable,                    # Check if retryable
)
```
