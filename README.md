# MCP Hangar

[![PyPI](https://img.shields.io/pypi/v/mcp-hangar)](https://pypi.org/project/mcp-hangar/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Production-grade infrastructure for Model Context Protocol.**

MCP Hangar is a control plane for MCP servers. It manages provider lifecycle, parallel tool execution, security governance, and observability -- so you don't have to.

## Quick Start

**30 seconds to working MCP providers:**

```bash
curl -sSL https://mcp-hangar.io/install.sh | bash && mcp-hangar init -y && mcp-hangar serve
```

That's it. Filesystem, fetch, and memory providers are now available to Claude.

<details>
<summary>What just happened?</summary>

1. **Install** - Downloaded and installed `mcp-hangar` via pip/uv
2. **Init** - Created `~/.config/mcp-hangar/config.yaml` with starter providers
3. **Serve** - Started the MCP server (stdio mode for Claude Desktop)

The `init -y` flag uses sensible defaults:

- Detects available runtimes (uvx preferred, npx fallback)
- Configures starter bundle: filesystem, fetch, memory
- Runs a smoke test to verify providers start correctly
- Updates Claude Desktop config automatically

</details>

### Manual Setup

```bash
# 1. Install
pip install mcp-hangar
# or: uv pip install mcp-hangar

# 2. Initialize with wizard
mcp-hangar init

# 3. Start server
mcp-hangar serve
```

### HTTP Mode

```bash
# Start with HTTP transport and REST API
mcp-hangar serve --http --port 8000

# REST API:  http://localhost:8000/api/
```

## What It Does

**Parallel execution.** Your AI agent calls 5 tools sequentially -- each takes 200ms, that's 1 second of waiting. `hangar_call` runs them in parallel. 200ms total.

```
hangar_call(calls=[
    {"provider": "github", "tool": "search_repos", "arguments": {"query": "mcp"}},
    {"provider": "slack", "tool": "post_message", "arguments": {"channel": "#dev"}},
    {"provider": "internal-api", "tool": "get_status", "arguments": {}}
])
```

Single MCP tool call. Parallel execution. All results returned together.

**Lifecycle management.** Lazy loading, health checks, automatic restart, graceful shutdown. Providers start on first use, stay warm while active, shut down after idle TTL.

**Single-flight cold starts.** When 10 parallel calls hit a cold provider, it initializes once -- not 10 times.

**Circuit breaker.** One failing provider doesn't kill your batch. Automatic isolation and recovery.

## Configuration

```yaml
providers:
  github:
    mode: subprocess
    command: [uvx, mcp-server-github]
    env:
      GITHUB_TOKEN: ${GITHUB_TOKEN}

  slack:
    mode: subprocess
    command: [uvx, mcp-server-slack]

  internal-api:
    mode: remote
    endpoint: "http://localhost:8080"

  custom-server:
    mode: docker
    image: my-registry/mcp-server:latest
    container:
      command: ["python", "-m", "custom_entrypoint"]
```

### Claude Desktop Integration

`mcp-hangar init` auto-configures Claude Desktop. For manual setup, add to your Claude Desktop config:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Linux:** `~/.config/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "hangar": {
      "command": "mcp-hangar",
      "args": ["serve", "--config", "~/.config/mcp-hangar/config.yaml"]
    }
  }
}
```

Restart Claude Desktop. Done.

## Python API

For programmatic use (scripts, pipelines, custom integrations):

```python
from mcp_hangar import Hangar, HangarConfig

# Async
async with Hangar.from_config("config.yaml") as hangar:
    result = await hangar.invoke("math", "add", {"a": 1, "b": 2})

# Sync wrapper
from mcp_hangar import SyncHangar

with SyncHangar.from_config("config.yaml") as hangar:
    result = hangar.invoke("math", "add", {"a": 1, "b": 2})

# Programmatic config
config = (
    HangarConfig()
    .add_provider("math", command=["python", "-m", "math_server"])
    .add_provider("fetch", mode="docker", image="mcp/fetch:latest")
    .build()
)
hangar = Hangar(config)
```

## Security & Governance (1.0)

- **Capability declaration.** Declare what each provider can access (network, filesystem, environment). Violations are detected and reported.
- **Behavioral profiling.** Baseline provider behavior, detect deviations (new destinations, protocol drift, frequency anomalies). Learning and enforcing modes.
- **Tool schema drift detection.** Track tool schema changes across provider updates.
- **Network connection monitoring.** `/proc/net/tcp` parsing, Docker and Kubernetes monitors with audit events.
- **RBAC.** Role-based access control with tool-level policies. API key and JWT/OIDC authentication.
- **Approval gate.** Human-in-the-loop approval for sensitive tool calls.

## Observability

- **OpenTelemetry.** Distributed tracing with W3C trace context propagation across providers.
- **Prometheus metrics.** Provider state, tool calls, health checks, circuit breaker, concurrency, batch execution.
- **Grafana dashboards.** Pre-built overview and per-provider deep dive dashboards.
- **Structured logging.** Correlation IDs across parallel calls. JSON log format for production.
- **Audit trail.** Event-sourced audit log with OTLP export for security-relevant events.

## Advanced Configuration

```yaml
providers:
  fast-provider:
    mode: subprocess
    command: ["python", "fast.py"]
    idle_ttl_s: 300              # Shutdown after 5min idle
    health_check_interval_s: 60  # Check health every minute
    max_consecutive_failures: 3  # Circuit breaker threshold
    max_concurrency: 5           # Per-provider concurrency limit
    tools:
      deny_list: [delete_*]      # Tool access filtering

execution:
  max_concurrency: 50            # Global concurrency limit
  default_provider_concurrency: 10

truncation:
  enabled: true
  max_batch_size_bytes: 950000   # Under Claude's 1MB limit

config_reload:
  enabled: true                  # Live config reload via file watch
```

## Scales With You

- **Home lab:** 2 providers, zero config complexity
- **Team setup:** Shared providers, Docker containers, hot-reload
- **Enterprise:** 50+ providers, behavioral profiling, RBAC, approval gates, Kubernetes operator

Same API. Same reliability. Different scale.

## Documentation

- [Getting Started](https://www.mcp-hangar.io/docs/oss/getting-started/quickstart)
- [Configuration Reference](https://mcp-hangar.io/reference/configuration/)
- [REST API Guide](https://www.mcp-hangar.io/docs/oss/reference/configuration)
- [Observability Setup](https://www.mcp-hangar.io/docs/oss/guides/OBSERVABILITY)
- [Authentication & RBAC](https://www.mcp-hangar.io/docs/oss/guides/AUTHENTICATION)
- [Cookbook](https://www.mcp-hangar.io/docs/oss/cookbook/)

## License

Core (`src/`) is MIT licensed. Enterprise features (`enterprise/`) are BSL 1.1 licensed.

See [LICENSE](LICENSE) for MIT terms and [enterprise/LICENSE.BSL](enterprise/LICENSE.BSL) for BSL terms.

---

[Docs](https://mcp-hangar.io) | [PyPI](https://pypi.org/project/mcp-hangar/) | [GitHub](https://github.com/mcp-hangar/mcp-hangar)
