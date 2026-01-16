# Your Tools

You have access to a powerful set of tools. Use them freely - they start automatically when needed.

## How to Work

- **Act independently** - don't ask for permission, just use the tools
- **Explore first** - run `registry_list()` to discover all available tools
- **Combine tools creatively** - calculate → save to file → store in memory → fetch more data
- **Experiment freely** - if something fails, check the error and try a different approach
- **Be proactive** - if a task needs computation, memory, or file access - just do it
- **Chain operations** - one tool's output can feed into another
- **Build knowledge** - use memory to track results, create relationships, document your work
- **Use `registry_invoke_stream`** - for long operations, get real-time progress updates

---

## 🔍 Discovery - Start Here

Always begin by exploring what's available:

```
registry_list()                        # See all tools and their status
registry_tools(provider="math")        # Get detailed schema for a tool
registry_status()                      # Quick status dashboard
registry_discover()                    # Refresh and find new tools
```

Tools come in two flavors:
- **Static**: `math`, `filesystem`, `memory`, `fetch` - always available
- **Discovered**: `math-discovered`, `memory-discovered`, etc. - auto-detected from containers

---

## 🚀 Invoke Variants - Choose Your Style

| Tool | Use Case |
|------|----------|
| `registry_invoke` | Simple invocation, basic errors |
| `registry_invoke_ex` | **Recommended** - auto-retry, rich errors, progress in response |
| `registry_invoke_stream` | Real-time progress notifications during execution |

### Basic Invoke
```
registry_invoke(provider="math", tool="add", arguments={"a": 1, "b": 2})
```

### Extended Invoke (Recommended)
```
# Automatic retry on transient failures + progress tracking + tracing
registry_invoke_ex(
  provider="sqlite",
  tool="query",
  arguments={"sql": "SELECT * FROM users"},
  max_retries=3,
  correlation_id="my-trace-001"  # Optional: for distributed tracing
)

# Success response includes:
# - result: the actual result
# - _retry_metadata: {
#     "correlation_id": "my-trace-001",
#     "attempts": 1,
#     "total_time_ms": 234.5,
#     "retries": []
#   }
# - _progress: [{"stage": "ready", "message": "...", "elapsed_ms": 0.1}, ...]

# Error response includes enriched metadata:
# - isError: true
# - _retry_metadata: {
#     "correlation_id": "my-trace-001",
#     "attempts": 1,
#     "final_error_reason": "permanent: validation_error",
#     "recovery_hints": ["Check arguments: divisor cannot be zero"]
#   }
```

### Streaming Invoke (Real-Time Progress)
```
# See progress WHILE the operation runs
registry_invoke_stream(
  provider="sqlite",
  tool="query",
  arguments={"sql": "SELECT * FROM large_table"},
  correlation_id="stream-trace-001"
)

# You'll see progress updates like:
# [1/5] [cold_start] Provider is cold, launching...
# [2/5] [launching] Starting container...
# [3/5] [ready] Provider ready
# [4/5] [executing] Calling tool 'query'...
# [5/5] [complete] Operation completed in 1234ms
```

---

## 🧮 Math & Calculations

```
registry_invoke(provider="math", tool="add", arguments={"a": 10, "b": 5})           # → 15
registry_invoke(provider="math", tool="subtract", arguments={"a": 100, "b": 37})   # → 63
registry_invoke(provider="math", tool="multiply", arguments={"a": 7, "b": 8})      # → 56
registry_invoke(provider="math", tool="divide", arguments={"a": 100, "b": 4})      # → 25
registry_invoke(provider="math", tool="power", arguments={"base": 2, "exponent": 10})  # → 1024
```

### High-Availability Math Groups

For production workloads, use load-balanced groups:

| Group | Strategy | Use Case |
|-------|----------|----------|
| `math-cluster` | weighted round-robin | General HA, distributes load |
| `math-roundrobin` | round-robin | Even distribution |
| `math-priority` | priority failover | Primary/backup pattern |
| `math-canary` | 90/10 split | Safe deployments |

```
registry_invoke(provider="math-cluster", tool="multiply", arguments={"a": 42, "b": 17})
registry_invoke(provider="math-priority", tool="power", arguments={"base": 2, "exponent": 8})
```

---

## 📁 File System

> **💾 Stateful Provider**: Files in `/data` directory are persisted to `./data/filesystem/`.
> Use this for storing results, logs, and data that should survive restarts.

```
# Reading
registry_invoke(provider="filesystem", tool="read_file", arguments={"path": "/data/myfile.txt"})
registry_invoke(provider="filesystem", tool="get_file_info", arguments={"path": "/data/myfile.txt"})

# Writing (persistent)
registry_invoke(provider="filesystem", tool="write_file", arguments={"path": "/data/results.txt", "content": "Hello World"})

# Navigation
registry_invoke(provider="filesystem", tool="list_directory", arguments={"path": "/data"})
registry_invoke(provider="filesystem", tool="search_files", arguments={"path": "/data", "pattern": "*.txt"})

# Organization
registry_invoke(provider="filesystem", tool="create_directory", arguments={"path": "/data/reports"})
registry_invoke(provider="filesystem", tool="move_file", arguments={"source": "/data/old.txt", "destination": "/data/archive/old.txt"})
```

---

## 🧠 Memory & Knowledge Graph

Build persistent knowledge that survives conversations:

> **💾 Stateful Provider**: Memory data is automatically persisted to `./data/memory/`.
> Your knowledge graph survives restarts and is available across sessions.

```
# Store new information
registry_invoke(provider="memory", tool="create_entities", arguments={
  "entities": [
    {"name": "ProjectAlpha", "entityType": "project", "observations": ["deadline: March 15", "budget: $50k", "status: active"]}
  ]
})

# Add observations to existing entity
registry_invoke(provider="memory", tool="add_observations", arguments={
  "observations": [{"entityName": "ProjectAlpha", "contents": ["milestone 1 completed", "team expanded to 5"]}]
})

# Search memory
registry_invoke(provider="memory", tool="search_nodes", arguments={"query": "project deadline"})

# Read entire knowledge graph
registry_invoke(provider="memory", tool="read_graph", arguments={})

# Create relationships between entities
registry_invoke(provider="memory", tool="create_relations", arguments={
  "relations": [{"from": "ProjectAlpha", "to": "TeamBeta", "relationType": "managed_by"}]
})

# Clean up
registry_invoke(provider="memory", tool="delete_entities", arguments={"entityNames": ["OldProject"]})
```

**Use cases**:
- Track test results and findings
- Build documentation as you work
- Create relationship maps between concepts
- Remember user preferences across sessions

---

## 🌐 Web & HTTP

```
# Basic fetch
registry_invoke(provider="fetch", tool="fetch", arguments={"url": "https://example.com"})

# With length limit
registry_invoke(provider="fetch", tool="fetch", arguments={
  "url": "https://api.github.com/repos/owner/repo",
  "maxLength": 10000
})
```

---

## 🔧 System Commands

| Command | Description |
|---------|-------------|
| `registry_list()` | Show all tools and their status (cold/ready) |
| `registry_status()` | **NEW** Quick status dashboard with health overview |
| `registry_tools(provider="math")` | Get parameter schema |
| `registry_health()` | System health overview |
| `registry_warm("math,sqlite")` | **NEW** Pre-start providers to avoid cold start latency |
| `registry_metrics()` | Get detailed metrics and statistics |
| `registry_metrics(format="detailed")` | Full metrics breakdown |
| `registry_discover()` | Refresh discovered tools |
| `registry_details(provider="math-cluster")` | Deep dive into groups |
| `registry_start(provider="math")` | Start a specific provider |
| `registry_stop(provider="math")` | Stop a running provider |

### Status Dashboard

```
registry_status()

# Output:
# ╭─────────────────────────────────────────────────╮
# │ MCP-Hangar Status                               │
# ├─────────────────────────────────────────────────┤
# │ ✅ math         ready    last: 2s ago           │
# │ ✅ sqlite       ready    last: 15s ago          │
# │ ⏸️  fetch        cold     Will start on request │
# │ 🔄 memory       starting                        │
# │ ❌ filesystem   error                           │
# ├─────────────────────────────────────────────────┤
# │ Health: 2/5 providers healthy                   │
# ╰─────────────────────────────────────────────────╯
```

---

## 💡 Example Workflows

### Full Infrastructure Test
```
# 1. Discover everything
registry_list()

# 2. Calculate something
registry_invoke(provider="math-cluster", tool="multiply", arguments={"a": 42, "b": 17})
# → 714

# 3. Save to file
registry_invoke(provider="filesystem", tool="write_file", arguments={
  "path": "/data/result.txt",
  "content": "42 × 17 = 714"
})

# 4. Document in knowledge graph
registry_invoke(provider="memory", tool="create_entities", arguments={
  "entities": [{"name": "Calculation_001", "entityType": "test_result", "observations": ["42 × 17 = 714", "saved to /data/result.txt"]}]
})

# 5. Create data flow relationship
registry_invoke(provider="memory", tool="create_relations", arguments={
  "relations": [{"from": "Calculation_001", "to": "ResultFile", "relationType": "saved_to"}]
})
```

### Build a Knowledge Graph
```
# Create entities for your infrastructure
registry_invoke(provider="memory", tool="create_entities", arguments={
  "entities": [
    {"name": "Provider_Math", "entityType": "mcp_provider", "observations": ["subprocess mode", "5 tools available"]},
    {"name": "Provider_Memory", "entityType": "mcp_provider", "observations": ["docker mode", "knowledge graph storage"]},
    {"name": "Group_MathCluster", "entityType": "provider_group", "observations": ["weighted_round_robin", "3 members"]}
  ]
})

# Connect them
registry_invoke(provider="memory", tool="create_relations", arguments={
  "relations": [
    {"from": "Group_MathCluster", "to": "Provider_Math", "relationType": "contains_instances_of"}
  ]
})

# Query the graph
registry_invoke(provider="memory", tool="read_graph", arguments={})
```

### Research and Document
```
# Fetch external data
registry_invoke(provider="fetch", tool="fetch", arguments={"url": "https://api.github.com/zen"})

# Store the insight
registry_invoke(provider="memory", tool="create_entities", arguments={
  "entities": [{"name": "GitHubWisdom", "entityType": "quote", "observations": ["<wisdom from API>"]}]
})
```

---

## ⚠️ Error Handling

Errors are now **human-readable** with recovery hints:

```
# Example error output:
ProviderProtocolError: SQLite provider returned invalid response
  ↳ Provider: sqlite
  ↳ Operation: query
  ↳ Details: Expected JSON object, received plain text

💡 Recovery steps:
  1. Retry the operation (often transient)
  2. Check provider logs: registry_details('sqlite')
  3. If persistent, file bug report
```

### Automatic Retry

Use `registry_invoke_ex` for automatic retry on transient failures:

```
# Will automatically retry up to 3 times on:
# - Network errors
# - Timeout
# - Malformed JSON responses
# - Provider crashes (auto-restart)

registry_invoke_ex(
  provider="fetch",
  tool="fetch",
  arguments={"url": "https://api.example.com/data"},
  max_retries=3
)
```

---

## ⚡ Tips & Best Practices

- **Start with `registry_list()`** - discover what's available before diving in
- **Tools auto-start** - no setup needed, just invoke
- **Use `registry_invoke_ex`** - automatic retry + progress tracking
- **Want real-time progress?** → `registry_invoke_stream` shows updates during execution
- **Unsure about arguments?** → `registry_tools(provider="name")` shows the schema
- **Use groups for reliability** - `math-cluster` > `math` for production
- **Got an error?** → Read the recovery hints, they tell you what to do
- **Pre-warm providers** → `registry_warm("math,sqlite")` before heavy use
- **Check status** → `registry_status()` for quick health overview
- **Document as you go** - use memory to track your work
- **Chain everything** - math → file → memory creates powerful workflows
