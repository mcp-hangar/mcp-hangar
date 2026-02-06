# MCP Hangar

[![PyPI](https://img.shields.io/pypi/v/mcp-hangar)](https://pypi.org/project/mcp-hangar/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Parallel MCP tool execution. One interface. 50x faster.**

## The Problem

Your AI agent calls 5 tools sequentially. Each takes 200ms. That's 1 second of waiting.

Hangar runs them in parallel. 200ms total. Same results, 50x faster.

## Quick Start

**30 seconds to working MCP providers:**

```bash
# Install, configure, and start - zero interaction
curl -sSL https://get.mcp-hangar.io | bash && mcp-hangar init -y && mcp-hangar serve
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
- Updates Claude Desktop config automatically

</details>

### Manual Setup

If you prefer step-by-step:

```bash
# 1. Install
pip install mcp-hangar
# or: uv pip install mcp-hangar

# 2. Initialize with wizard
mcp-hangar init

# 3. Start server
mcp-hangar serve
```

### Custom Configuration

Create `~/.config/mcp-hangar/config.yaml`:

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
```

Claude Desktop is auto-configured by `mcp-hangar init`. Manual setup:

**Add to Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

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

## One Interface

```python
hangar_call([
    {"provider": "github", "tool": "search_repos", "arguments": {"query": "mcp"}},
    {"provider": "slack", "tool": "post_message", "arguments": {"channel": "#dev"}},
    {"provider": "internal-api", "tool": "get_status", "arguments": {}}
])
```

Single call. Parallel execution. All results returned together.

## Benchmarks

| Scenario | Sequential | Hangar | Speedup |
|----------|-----------|--------|---------|
| 15 tools, 2 providers | ~20s | 380ms | 50x |
| 50 concurrent requests | ~15s | 1.3s | 10x |
| Cold start + batch | ~5s | <500ms | 10x |

100% success rate. <10ms framework overhead.

## Why It's Fast

**Single-flight cold starts.** When 10 parallel calls hit a cold provider, it initializes once — not 10 times.

**Automatic concurrency.** Configurable parallelism with backpressure. No thundering herd.

**Provider pooling.** Hot providers stay warm. Cold providers spin up on demand, shut down after idle TTL.

## Production Ready

**Lifecycle management.** Lazy loading, health checks, automatic restart, graceful shutdown.

**Circuit breaker.** One failing provider doesn't kill your batch. Automatic isolation and recovery.

**Observability.** Correlation IDs across parallel calls. OpenTelemetry traces, Prometheus metrics.

**Multi-provider.** Subprocess, Docker, remote HTTP — mix them in a single batch call.

## Configuration

```yaml
providers:
  - id: fast-provider
    command: ["python", "fast.py"]
    idle_ttl_s: 300              # Shutdown after 5min idle
    health_check_interval_s: 60  # Check health every minute
    max_consecutive_failures: 3  # Circuit breaker threshold

  - id: docker-provider
    image: my-registry/mcp-server:latest
    network: bridge

  - id: remote-provider
    url: "https://api.example.com/mcp"
```

## Works Everywhere

- **Home lab:** 2 providers, zero config complexity
- **Team setup:** Shared providers, Docker containers
- **Enterprise:** 50+ providers, observability stack, Kubernetes

Same API. Same reliability. Different scale.

## Documentation

- [Getting Started](https://mcp-hangar.io/getting-started/)
- [Configuration Reference](https://mcp-hangar.io/configuration/)
- [Claude Code Integration](https://mcp-hangar.io/guides/claude-code/)
- [Observability Setup](https://mcp-hangar.io/guides/observability/)

## License

MIT — use it, fork it, ship it.

---

[Docs](https://mcp-hangar.io) · [PyPI](https://pypi.org/project/mcp-hangar/) · [GitHub](https://github.com/mapyr/mcp-hangar)
