# MCP Hangar

[![Tests](https://github.com/mapyr/mcp-hangar/actions/workflows/test.yml/badge.svg)](https://github.com/mapyr/mcp-hangar/actions/workflows/test.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Production-grade MCP provider registry with lazy loading, health monitoring, and container support.

## Features

- **Lazy Loading** — Providers start only when invoked, tools visible immediately
- **Container Support** — Docker/Podman with auto-detection
- **Provider Groups** — Load balancing with multiple strategies
- **Health Monitoring** — Circuit breaker pattern with automatic recovery
- **Auto-Discovery** — Detect providers from Docker labels, K8s annotations, filesystem
- **Automatic Retry** — Built-in retry with exponential backoff for transient failures
- **Real-Time Progress** — See operation progress while waiting (`registry_invoke_stream`)
- **Rich Errors** — Human-readable errors with recovery hints

## Quick Start

```bash
uv pip install .
python -m mcp_hangar.server --config config.yaml
```

### Claude Desktop

```json
{
  "mcpServers": {
    "mcp-hangar": {
      "command": "python",
      "args": ["-m", "mcp_hangar.server", "--config", "/path/to/config.yaml"],
      "cwd": "/path/to/mcp-hangar"
    }
  }
}
```

### HTTP Mode (LM Studio)

```bash
python -m mcp_hangar.server --http
# Server at http://localhost:8000/mcp
```

## Configuration

```yaml
providers:
  # Subprocess
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

  # Container
  sqlite:
    mode: container
    image: localhost/mcp-sqlite:latest
    volumes:
      - "/absolute/path/to/data:/data:rw"  # Must be absolute
    network: bridge
    idle_ttl_s: 300
```

> **Note**: Use absolute paths for volume mounts. Relative paths fail when MCP clients start the server from different directories.

### Building Container Images

```bash
podman build -t localhost/mcp-sqlite -f docker/Dockerfile.sqlite .
podman build -t localhost/mcp-memory -f docker/Dockerfile.memory .
podman build -t localhost/mcp-filesystem -f docker/Dockerfile.filesystem .
podman build -t localhost/mcp-fetch -f docker/Dockerfile.fetch .

mkdir -p data/sqlite data/memory data/filesystem
```

## Registry Tools

| Tool                     | Description                       |
|--------------------------|-----------------------------------|
| `registry_list`          | List providers                    |
| `registry_start`         | Start provider                    |
| `registry_stop`          | Stop provider                     |
| `registry_invoke`        | Invoke tool                       |
| `registry_invoke_ex`     | Invoke with retry & progress      |
| `registry_invoke_stream` | Invoke with real-time progress ⭐ |
| `registry_tools`         | Get tool schemas                  |
| `registry_details`       | Provider details                  |
| `registry_health`        | Health status                     |
| `registry_status`        | Status dashboard                  |
| `registry_warm`          | Pre-start providers               |

### Examples

```python
# List providers (containers stay OFF)
registry_list()

# Get tools (container still OFF)
registry_tools(provider="sqlite")

# Invoke tool (starts container, executes)
registry_invoke(provider="sqlite", tool="execute",
                arguments={"sql": "CREATE TABLE users (id INTEGER PRIMARY KEY)"})

registry_invoke(provider="sqlite", tool="query",
                arguments={"sql": "SELECT * FROM users"})

# With automatic retry and correlation_id (recommended for production)
registry_invoke_ex(provider="sqlite", tool="query",
                   arguments={"sql": "SELECT * FROM users"},
                   max_retries=3,
                   correlation_id="my-trace-001")
# Returns: _retry_metadata with correlation_id, attempts, retries
# On error: includes final_error_reason and recovery_hints

# With real-time progress notifications (model sees progress!)
registry_invoke_stream(provider="sqlite", tool="query",
                       arguments={"sql": "SELECT * FROM large_table"},
                       correlation_id="stream-trace-001")
# Progress: [1/5] [cold_start] Provider is cold, launching...
# Progress: [2/5] [launching] Starting subprocess provider...
# Progress: [3/5] [ready] Provider ready
# Progress: [4/5] [executing] Calling tool 'query'...
# Progress: [5/5] [complete] Operation completed in 234ms

# Status dashboard
registry_status()
# ✅ math    ready   last: 2s ago
# ⏸️  sqlite  cold    Will start on request

# Pre-warm providers to avoid cold start latency
registry_warm("math,sqlite")
```

## Provider Groups

```yaml
providers:
  math-cluster:
    mode: group
    strategy: weighted_round_robin
    min_healthy: 2
    members:
      - id: math-1
        weight: 3
        mode: subprocess
        command: [python, -m, examples.provider_math.server]
      - id: math-2
        weight: 1
        mode: subprocess
        command: [python, -m, examples.provider_math.server]
```

Strategies: `round_robin`, `weighted_round_robin`, `random`, `priority`, `least_connections`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_CONFIG` | `config.yaml` | Config file path |
| `MCP_HTTP_PORT` | `8000` | HTTP server port |
| `MCP_RATE_LIMIT_RPS` | `10` | Rate limit |
| `MCP_CONTAINER_RUNTIME` | auto | Force `podman` or `docker` |

## Development

```bash
git clone https://github.com/mapyr/mcp-hangar.git
cd mcp-hangar
uv sync --extra dev
uv run pytest tests/ -v -m "not slow"
```

## Documentation

- [Container Guide](docs/guides/CONTAINERS.md)
- [Discovery](docs/guides/DISCOVERY.md)
- [Observability](docs/guides/OBSERVABILITY.md) - Metrics, tracing, health checks
- [UX Improvements](docs/guides/UX_IMPROVEMENTS.md) - Retry, progress, rich errors
- [Architecture](docs/architecture/OVERVIEW.md)
- [Testing](docs/guides/TESTING.md)
- [Contributing](docs/development/CONTRIBUTING.md)

## License

MIT
