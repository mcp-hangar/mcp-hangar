# Your Tools

You have access to a powerful set of tools. Use them freely - they start automatically when needed.

## How to Work

- **Act independently** - don't ask for permission, just use the tools
- **Explore first** - run `hangar_list()` to discover all available tools
- **Combine tools creatively** - calculate, save to file, store in memory, fetch more data
- **Experiment freely** - if something fails, check the error and try a different approach
- **Be proactive** - if a task needs computation, memory, or file access - just do it
- **Chain operations** - one tool's output can feed into another
- **Build knowledge** - use memory to track results, create relationships, document your work
- **Use `hangar_call` with retry** - for reliability, set `max_retries` parameter

---

## Discovery - Start Here

Always begin by exploring what's available:

```
hangar_list()                        # See all tools and their status
hangar_tools(provider="math")        # Get detailed schema for a tool
hangar_status()                      # Quick status dashboard
hangar_discover()                    # Refresh and find new tools
```

Tools come in two flavors:
- **Static**: `math`, `filesystem`, `memory`, `fetch` - always available
- **Discovered**: `math-discovered`, `memory-discovered`, etc. - auto-detected from containers

---

## Unified Invocation API: hangar_call

All tool invocations use a single unified API: `hangar_call`. Whether you're making one call or many, the format is consistent.

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

### Batch Invocation (Parallel Execution)

```python
# Execute multiple calls in parallel - much faster than sequential
hangar_call(calls=[
    {"provider": "math", "tool": "add", "arguments": {"a": 1, "b": 2}},
    {"provider": "math", "tool": "multiply", "arguments": {"a": 3, "b": 4}},
    {"provider": "fetch", "tool": "get", "arguments": {"url": "https://api.github.com"}},
])

# Response:
# {
#   "batch_id": "...",
#   "success": true,
#   "total": 3,
#   "succeeded": 3,
#   "failed": 0,
#   "elapsed_ms": 234.5,
#   "results": [
#     {"index": 0, "call_id": "...", "success": true, "result": {"sum": 3}},
#     {"index": 1, "call_id": "...", "success": true, "result": {"product": 12}},
#     {"index": 2, "call_id": "...", "success": true, "result": {...}}
#   ]
# }
```

### API Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `calls` | `list[dict]` | required | List of call specifications |
| `max_concurrency` | `int` | 10 | Maximum parallel workers (1-20) |
| `timeout` | `float` | 60.0 | Global timeout in seconds (1-300) |
| `fail_fast` | `bool` | False | Abort remaining calls on first error |
| `max_retries` | `int` | 1 | Retry attempts per call (1-10) |

### With Retry (Recommended)

```python
# Automatic retry on transient failures
hangar_call(
    calls=[{"provider": "sqlite", "tool": "query", "arguments": {"sql": "SELECT * FROM users"}}],
    max_retries=3
)

# Success response includes retry metadata:
# {
#   "results": [{
#     "success": true,
#     "result": {...},
#     "retry_metadata": {
#       "attempts": 2,
#       "retries": ["TimeoutError"],
#       "total_time_ms": 1234.5
#     }
#   }]
# }
```

### With Fail-Fast

```python
# Abort on first error
hangar_call(
    calls=[...],
    fail_fast=True
)
```

### With Per-Call Timeout

```python
hangar_call(calls=[
    {"provider": "fetch", "tool": "get", "arguments": {"url": "..."}, "timeout": 5.0},
], timeout=60.0)
```

---

## Math & Calculations

```python
hangar_call(calls=[{"provider": "math", "tool": "add", "arguments": {"a": 10, "b": 5}}])           # 15
hangar_call(calls=[{"provider": "math", "tool": "subtract", "arguments": {"a": 100, "b": 37}}])   # 63
hangar_call(calls=[{"provider": "math", "tool": "multiply", "arguments": {"a": 7, "b": 8}}])      # 56
hangar_call(calls=[{"provider": "math", "tool": "divide", "arguments": {"a": 100, "b": 4}}])      # 25
hangar_call(calls=[{"provider": "math", "tool": "power", "arguments": {"base": 2, "exponent": 10}}])  # 1024
```

### High-Availability Math Groups

For production workloads, use load-balanced groups:

| Group | Strategy | Use Case |
|-------|----------|----------|
| `math-cluster` | weighted round-robin | General HA, distributes load |
| `math-roundrobin` | round-robin | Even distribution |
| `math-priority` | priority failover | Primary/backup pattern |
| `math-canary` | 90/10 split | Safe deployments |

```python
hangar_call(calls=[{"provider": "math-cluster", "tool": "multiply", "arguments": {"a": 42, "b": 17}}])
hangar_call(calls=[{"provider": "math-priority", "tool": "power", "arguments": {"base": 2, "exponent": 8}}])
```

---

## File System

> **Stateful Provider**: Files in `/data` directory are persisted to `./data/filesystem/`.
> Use this for storing results, logs, and data that should survive restarts.

```python
# Reading
hangar_call(calls=[{"provider": "filesystem", "tool": "read_file", "arguments": {"path": "/data/myfile.txt"}}])
hangar_call(calls=[{"provider": "filesystem", "tool": "get_file_info", "arguments": {"path": "/data/myfile.txt"}}])

# Writing (persistent)
hangar_call(calls=[{"provider": "filesystem", "tool": "write_file", "arguments": {"path": "/data/results.txt", "content": "Hello World"}}])

# Navigation
hangar_call(calls=[{"provider": "filesystem", "tool": "list_directory", "arguments": {"path": "/data"}}])
hangar_call(calls=[{"provider": "filesystem", "tool": "search_files", "arguments": {"path": "/data", "pattern": "*.txt"}}])

# Organization
hangar_call(calls=[{"provider": "filesystem", "tool": "create_directory", "arguments": {"path": "/data/reports"}}])
hangar_call(calls=[{"provider": "filesystem", "tool": "move_file", "arguments": {"source": "/data/old.txt", "destination": "/data/archive/old.txt"}}])
```

---

## Memory & Knowledge Graph

Build persistent knowledge that survives conversations:

> **Stateful Provider**: Memory data is automatically persisted to `./data/memory/`.
> Your knowledge graph survives restarts and is available across sessions.

```python
# Store new information
hangar_call(calls=[{"provider": "memory", "tool": "create_entities", "arguments": {
  "entities": [
    {"name": "ProjectAlpha", "entityType": "project", "observations": ["deadline: March 15", "budget: $50k", "status: active"]}
  ]
}}])

# Add observations to existing entity
hangar_call(calls=[{"provider": "memory", "tool": "add_observations", "arguments": {
  "observations": [{"entityName": "ProjectAlpha", "contents": ["milestone 1 completed", "team expanded to 5"]}]
}}])

# Search memory
hangar_call(calls=[{"provider": "memory", "tool": "search_nodes", "arguments": {"query": "project deadline"}}])

# Read entire knowledge graph
hangar_call(calls=[{"provider": "memory", "tool": "read_graph", "arguments": {}}])

# Create relationships between entities
hangar_call(calls=[{"provider": "memory", "tool": "create_relations", "arguments": {
  "relations": [{"from": "ProjectAlpha", "to": "TeamBeta", "relationType": "managed_by"}]
}}])

# Clean up
hangar_call(calls=[{"provider": "memory", "tool": "delete_entities", "arguments": {"entityNames": ["OldProject"]}}])
```

**Use cases**:
- Track test results and findings
- Build documentation as you work
- Create relationship maps between concepts
- Remember user preferences across sessions

---

## Web & HTTP

```python
# Basic fetch
hangar_call(calls=[{"provider": "fetch", "tool": "fetch", "arguments": {"url": "https://example.com"}}])

# With length limit
hangar_call(calls=[{"provider": "fetch", "tool": "fetch", "arguments": {
  "url": "https://api.github.com/repos/owner/repo",
  "maxLength": 10000
}}])
```

---

## System Commands

| Command | Description |
|---------|-------------|
| `hangar_list()` | Show all tools and their status (cold/ready) |
| `hangar_status()` | Quick status dashboard with health overview |
| `hangar_tools(provider="math")` | Get parameter schema |
| `hangar_health()` | System health overview |
| `hangar_warm("math,sqlite")` | Pre-start providers to avoid cold start latency |
| `hangar_metrics()` | Get detailed metrics and statistics |
| `hangar_metrics(format="detailed")` | Full metrics breakdown |
| `hangar_discover()` | Refresh discovered tools |
| `hangar_details(provider="math-cluster")` | Deep dive into groups |
| `hangar_start(provider="math")` | Start a specific provider |
| `hangar_stop(provider="math")` | Stop a running provider |

### Status Dashboard

```
hangar_status()

# Output:
# +---------------------------------------------------+
# | MCP-Hangar Status                                 |
# +---------------------------------------------------+
# | [OK] math         ready    last: 2s ago          |
# | [OK] sqlite       ready    last: 15s ago         |
# | [COLD]  fetch     cold     Will start on request |
# | [STARTING] memory starting                       |
# | [ERROR] filesystem   error                       |
# +---------------------------------------------------+
# | Health: 2/5 providers healthy                    |
# +---------------------------------------------------+
```

---

## Example Workflows

### Full Infrastructure Test
```python
# 1. Discover everything
hangar_list()

# 2. Calculate something
hangar_call(calls=[{"provider": "math-cluster", "tool": "multiply", "arguments": {"a": 42, "b": 17}}])
# result: 714

# 3. Save to file
hangar_call(calls=[{"provider": "filesystem", "tool": "write_file", "arguments": {
  "path": "/data/result.txt",
  "content": "42 x 17 = 714"
}}])

# 4. Document in knowledge graph
hangar_call(calls=[{"provider": "memory", "tool": "create_entities", "arguments": {
  "entities": [{"name": "Calculation_001", "entityType": "test_result", "observations": ["42 x 17 = 714", "saved to /data/result.txt"]}]
}}])

# 5. Create data flow relationship
hangar_call(calls=[{"provider": "memory", "tool": "create_relations", "arguments": {
  "relations": [{"from": "Calculation_001", "to": "ResultFile", "relationType": "saved_to"}]
}}])
```

### Parallel Operations

```python
# Execute multiple operations in parallel
hangar_call(calls=[
    {"provider": "math", "tool": "add", "arguments": {"a": 100, "b": 200}},
    {"provider": "math", "tool": "multiply", "arguments": {"a": 50, "b": 4}},
    {"provider": "fetch", "tool": "fetch", "arguments": {"url": "https://api.github.com/zen"}},
    {"provider": "filesystem", "tool": "read_file", "arguments": {"path": "/data/config.txt"}},
], max_concurrency=4)
```

### Build a Knowledge Graph
```python
# Create entities for your infrastructure
hangar_call(calls=[{"provider": "memory", "tool": "create_entities", "arguments": {
  "entities": [
    {"name": "Provider_Math", "entityType": "mcp_provider", "observations": ["subprocess mode", "5 tools available"]},
    {"name": "Provider_Memory", "entityType": "mcp_provider", "observations": ["docker mode", "knowledge graph storage"]},
    {"name": "Group_MathCluster", "entityType": "provider_group", "observations": ["weighted_round_robin", "3 members"]}
  ]
}}])

# Connect them
hangar_call(calls=[{"provider": "memory", "tool": "create_relations", "arguments": {
  "relations": [
    {"from": "Group_MathCluster", "to": "Provider_Math", "relationType": "contains_instances_of"}
  ]
}}])

# Query the graph
hangar_call(calls=[{"provider": "memory", "tool": "read_graph", "arguments": {}}])
```

### Research and Document
```python
# Fetch external data
hangar_call(calls=[{"provider": "fetch", "tool": "fetch", "arguments": {"url": "https://api.github.com/zen"}}])

# Store the insight
hangar_call(calls=[{"provider": "memory", "tool": "create_entities", "arguments": {
  "entities": [{"name": "GitHubWisdom", "entityType": "quote", "observations": ["<wisdom from API>"]}]
}}])
```

---

## Error Handling

Errors are **human-readable** with recovery hints:

```
ProviderProtocolError: SQLite provider returned invalid response
    - Provider: sqlite
    - Operation: query
    - Details: Expected JSON object, received plain text

[TIP] Recovery steps:
  1. Retry the operation (often transient)
  2. Check provider logs: hangar_details('sqlite')
  3. If persistent, file bug report
```

### Automatic Retry

Use `max_retries` parameter for automatic retry on transient failures:

```python
# Will automatically retry up to 3 times on:
# - Network errors
# - Timeout
# - Malformed JSON responses
# - Provider crashes (auto-restart)

hangar_call(
    calls=[{"provider": "fetch", "tool": "fetch", "arguments": {"url": "https://api.example.com/data"}}],
    max_retries=3
)
```

---

## Tips & Best Practices

- **Start with `hangar_list()`** - discover what's available before diving in
- **Tools auto-start** - no setup needed, just invoke
- **Use `max_retries` for reliability** - handles transient failures automatically
- **Batch related calls** - `hangar_call(calls=[...])` executes in parallel
- **Unsure about arguments?** - `hangar_tools(provider="name")` shows the schema
- **Use groups for reliability** - `math-cluster` > `math` for production
- **Got an error?** - Read the recovery hints, they tell you what to do
- **Pre-warm providers** - `hangar_warm("math,sqlite")` before heavy use
- **Check status** - `hangar_status()` for quick health overview
- **Document as you go** - use memory to track your work
- **Chain everything** - math, file, memory creates powerful workflows
