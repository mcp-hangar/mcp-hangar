# MCP Hangar

[![Tests](https://github.com/mapyr/mcp-hangar/actions/workflows/test.yml/badge.svg)](https://github.com/mapyr/mcp-hangar/actions/workflows/test.yml)
[![Lint](https://github.com/mapyr/mcp-hangar/actions/workflows/lint.yml/badge.svg)](https://github.com/mapyr/mcp-hangar/actions/workflows/lint.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A production-grade registry for managing Model Context Protocol (MCP) providers with hot-loading, health monitoring, and automatic garbage collection.


## Features

- **Multiple Transport Modes**: Stdio (default) and HTTP with Streamable HTTP support
- **Container Support**: Docker and Podman with auto-detection
- **Pre-Built Images**: Use any Docker/Podman image directly
- **Provider Groups**: Load balancing and high availability with multiple strategies
- **Thread-Safe**: All operations protected with proper locking
- **Health Monitoring**: Active health checks with circuit breaker pattern
- **Garbage Collection**: Automatic shutdown of idle providers
- **State Machine**: `COLD → INITIALIZING → READY → DEGRADED → DEAD`

## Quick Start

### Installation

Using `uv` (recommended):

```bash
uv pip install .
```

Or using standard `pip`:

```bash
pip install .
```

### Running the Registry

#### Stdio Mode (Default)

For MCP clients that spawn processes (Claude Desktop, etc.):

```bash
python -m mcp_hangar.server
```

#### HTTP Mode

For HTTP-based MCP clients (LM Studio, web apps, etc.):

```bash
python -m mcp_hangar.server --http
```

Server will start on `http://localhost:8000/mcp`

### LM Studio Configuration

Create `mcp-config.json`:

```json
{
  "mcpServers": {
    "mcp-hangar": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Then in LM Studio: Settings → Developer → MCP Servers → Select the config file.

## Configuration

### Configuration File

Create `config.yaml`:

```yaml
providers:
  # Subprocess mode
  math:
    mode: subprocess
    command:
      - python
      - -m
      - examples.provider_math.server
    idle_ttl_s: 180

  # Container mode with pre-built image
  filesystem:
    mode: container
    image: ghcr.io/modelcontextprotocol/server-filesystem:latest
    volumes:
      - "${HOME}:/data:ro"

  # Container mode with custom build
  custom:
    mode: container
    build:
      dockerfile: docker/Dockerfile.custom
      context: .
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MCP_CONFIG` | Path to config file | `config.yaml` |
| `MCP_HTTP_HOST` | HTTP server host | `0.0.0.0` |
| `MCP_HTTP_PORT` | HTTP server port | `8000` |

## Registry Tools

The registry exposes these MCP tools:

| Tool | Description |
|------|-------------|
| `registry_list` | List all providers with status |
| `registry_start` | Start a provider |
| `registry_stop` | Stop a provider |
| `registry_invoke` | Invoke a tool on a provider |
| `registry_tools` | Get provider's tool schemas |
| `registry_details` | Get provider details |
| `registry_health` | Get registry health status |

### Example: Invoke a Tool

```python
# Through the registry
result = registry_invoke(
    provider="math",
    tool="add",
    arguments={"a": 5, "b": 3}
)
# Returns: {"result": 8}
```

## Provider Modes

### Subprocess Mode

Runs MCP server as a local subprocess:

```yaml
my_provider:
  mode: subprocess
  command:
    - python
    - -m
    - my_mcp_server
```

### Container Mode

Runs MCP server in Docker/Podman container:

```yaml
my_provider:
  mode: container
  image: my-mcp-server:latest
  # Or build from Dockerfile:
  build:
    dockerfile: Dockerfile
    context: .
  volumes:
    - "/host/path:/container/path:ro"
  env:
    MY_VAR: "value"
  resources:
    memory: 256m
    cpu: "0.5"
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       MCP Hangar                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │  Provider 1 │  │  Provider 2 │  │  Provider N │          │
│  │  (subprocess)│  │  (docker)   │  │  (podman)   │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
│         │                │                │                  │
│         └────────────────┴────────────────┘                  │
│                          │                                   │
│              ┌───────────┴───────────┐                       │
│              │    Provider Manager   │                       │
│              │  (State, Health, GC)  │                       │
│              └───────────────────────┘                       │
│                          │                                   │
│              ┌───────────┴───────────┐                       │
│              │     MCP Server        │                       │
│              │  (Stdio or HTTP)      │                       │
│              └───────────────────────┘                       │
└─────────────────────────────────────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              │      MCP Clients        │
              │ (LM Studio, Claude, etc)│
              └─────────────────────────┘
```

## Development

### Development Setup

To contribute to this project, you'll need [uv](https://github.com/astral-sh/uv) and a Docker runtime (Docker Desktop or Podman) installed.

#### 1. Install Dependencies

Standard installation only installs runtime dependencies. To run tests and linters, you must install the `dev` extras:

```bash
# Install all dependencies including pytest, ruff, etc.
uv sync --extra dev
```

#### 2. Prepare Docker Environment

Some integration tests (e.g., Memory Provider) run actual MCP servers in Docker containers. These containers need write access to the local `data` directory.

Before running tests, ensure the directory exists and has permissive permissions to avoid `EACCES: permission denied` errors inside the container:

```bash
# Create data directory and set permissions for Docker volume mounting
mkdir -p data
chmod 777 data
```

#### 3. Run Tests

Once dependencies are installed and permissions are set, you can run the full test suite:

```bash
# All tests
uv run pytest tests/ -v

# Quick tests only
uv run pytest tests/ -v -m "not slow"
```

### Project Structure

```
mcp_hangar/
├── server.py           # Main MCP server (stdio + http modes)
├── provider_manager.py # Provider lifecycle management
├── stdio_client.py     # JSON-RPC client for providers
├── gc.py               # Background health checks & GC
├── fastmcp_server.py   # HTTP server using FastMCP
├── http_server.py      # REST API server
└── mcp_sse.py          # SSE transport (legacy)
```

## Documentation

Full documentation is available in the [docs/](docs/INDEX.md) directory:

- [Getting Started](docs/INDEX.md#getting-started) - Quick start guide
- [Docker Support](docs/DOCKER_SUPPORT.md) - Container configuration
- [Pre-built Images](docs/PREBUILT_IMAGES.md) - Using existing Docker images
- [API Reference](docs/api/TOOLS_REFERENCE.md) - Registry tools API
- [Architecture](docs/architecture/OVERVIEW.md) - System design
- [Contributing](docs/development/CONTRIBUTING.md) - Development guide
- [Security](docs/SECURITY.md) - Security features
- [Troubleshooting](docs/TROUBLESHOOTING.md) - Common issues and solutions
- [FAQ](docs/FAQ.md) - Frequently asked questions

## License

MIT License - see [LICENSE](LICENSE)
