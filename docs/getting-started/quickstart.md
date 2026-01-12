# Quick Start

## Basic Usage

### 1. Create Configuration

Create a `config.yaml` file:

```yaml
providers:
  # Subprocess provider
  math:
    mode: subprocess
    command: [python, -m, examples.provider_math.server]
    idle_ttl_s: 180
    tools:
      - name: add
        description: "Add two numbers"
        inputSchema:
          type: object
          properties:
            a: { type: number }
            b: { type: number }
          required: [a, b]

  # Container provider
  sqlite:
    mode: container
    image: localhost/mcp-sqlite:latest
    volumes:
      - "/absolute/path/to/data:/data:rw"
    network: bridge
    idle_ttl_s: 300
```

!!! warning "Volume Paths"
    Always use absolute paths for volume mounts. Relative paths fail when MCP clients start the server from different directories.

### 2. Start the Server

```bash
# Stdio mode (for Claude Desktop, etc.)
mcp-hangar --config config.yaml

# HTTP mode (for LM Studio, web clients)
mcp-hangar --config config.yaml --http
# Server at http://localhost:8000/mcp
```

### 3. Configure Claude Desktop

Add to your Claude Desktop configuration:

```json
{
  "mcpServers": {
    "mcp-hangar": {
      "command": "mcp-hangar",
      "args": ["--config", "/path/to/config.yaml"]
    }
  }
}
```

## Registry Tools

Once connected, you have access to these tools:

| Tool | Description |
|------|-------------|
| `registry_list` | List all providers and their status |
| `registry_start` | Manually start a provider |
| `registry_stop` | Stop a running provider |
| `registry_invoke` | Invoke a tool on a provider |
| `registry_tools` | Get available tools for a provider |
| `registry_health` | Check provider health |
| `registry_status` | Dashboard view of all providers |

## Example Workflow

```python
# 1. List available providers (containers stay OFF)
registry_list()
# → math: cold, sqlite: cold

# 2. Check available tools (still cold)
registry_tools(provider="sqlite")
# → [execute, query, ...]

# 3. Invoke a tool (auto-starts provider)
registry_invoke(
    provider="sqlite",
    tool="execute",
    arguments={"sql": "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)"}
)
# Provider starts → executes → returns result

# 4. Check status
registry_status()
# → ✅ sqlite  ready   last: 2s ago
# → ⏸️  math    cold    Will start on request
```

## Next Steps

- [Container Guide](../guides/CONTAINERS.md) — Setting up container providers
- [Observability](../guides/OBSERVABILITY.md) — Metrics and monitoring
- [Architecture](../architecture/OVERVIEW.md) — Understanding the design
