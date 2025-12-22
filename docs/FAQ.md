# Frequently Asked Questions

## General

### What is MCP Hangar?

MCP Hangar is a production-grade registry for managing Model Context Protocol (MCP) providers. It handles provider lifecycle management, health monitoring, automatic garbage collection, and supports multiple transport modes (stdio and HTTP).

### What is MCP?

MCP (Model Context Protocol) is a protocol that allows AI assistants to interact with external tools and services. MCP Hangar acts as a central registry that manages multiple MCP providers, allowing you to organize and access various tools through a single interface.

### How is MCP Hangar different from running MCP servers directly?

Instead of configuring each MCP server separately in your AI client, MCP Hangar:

- **Centralizes management** - One configuration file for all providers
- **Adds hot-loading** - Providers start on-demand and shut down when idle
- **Provides health monitoring** - Automatic detection and recovery from failures
- **Offers isolation** - Run providers in containers for security
- **Enables HTTP access** - Use MCP over HTTP for web applications

### Which AI clients work with MCP Hangar?

MCP Hangar works with any MCP-compatible client:

- **LM Studio** - Via HTTP mode
- **Claude Desktop** - Via stdio mode
- **Custom applications** - Via HTTP REST API or stdio

---

## Installation

### What are the system requirements?

- Python 3.10 or higher
- Docker or Podman (optional, for container mode)
- macOS, Linux, or Windows

### How do I install MCP Hangar?

```bash
# Using uv (recommended)
uv pip install .

# Using pip
pip install .
```

### Do I need Docker?

No. Docker/Podman is only required if you want to run providers in containers. Subprocess mode works without any container runtime.

---

## Configuration

### Where is the configuration file?

By default, MCP Hangar looks for `config.yaml` in the current directory. You can specify a different path using the `MCP_CONFIG` environment variable:

```bash
MCP_CONFIG=/path/to/my-config.yaml python -m mcp_hangar.server
```

### How do I add a new provider?

Add a new entry to your `config.yaml`:

```yaml
providers:
  my_provider:
    mode: subprocess
    command:
      - python
      - -m
      - my_mcp_server
```

Then restart MCP Hangar or use `registry_start(provider="my_provider")`.

### What's the difference between subprocess and container mode?

| Mode | Use Case | Isolation | Startup |
|------|----------|-----------|---------|
| `subprocess` | Trusted local tools | Process-level | Fast |
| `container` | Untrusted code, reproducibility | Full container | Slower |

### How do I pass environment variables to providers?

```yaml
providers:
  my_provider:
    mode: subprocess
    command: [...]
    env:
      API_KEY: "${API_KEY}"  # From host environment
      FIXED_VALUE: "some-value"
```

---

## Usage

### How do I start MCP Hangar?

**Stdio mode** (for Claude Desktop and similar):
```bash
python -m mcp_hangar.server
```

**HTTP mode** (for LM Studio and web apps):
```bash
python -m mcp_hangar.server --http
```

### How do I invoke a tool?

Use `registry_invoke`:

```python
result = registry_invoke(
    provider="math",
    tool="add",
    arguments={"a": 5, "b": 3}
)
```

### Do I need to start providers manually?

No. Providers start automatically when you invoke a tool. You can optionally use `registry_start` for explicit control.

### What happens when a provider is idle?

Providers automatically shut down after being idle for the configured `idle_ttl_s` (default: 300 seconds). They restart automatically on the next tool invocation.

---

## Troubleshooting

### Provider won't start

1. Check that the command is correct:
   ```bash
   python -m my_mcp_server  # Test manually
   ```

2. Check logs for detailed error messages

3. Verify the provider implements the MCP protocol correctly

### "ProviderDegradedError" - what does it mean?

The provider has failed multiple times and entered a degraded state (circuit breaker activated). Wait for the backoff period to elapse, or restart MCP Hangar.

### Container provider fails with permission errors

1. Check volume mount permissions:
   ```yaml
   volumes:
     - "./data:/app/data:rw"
   ```

2. Try setting `user: "current"` to run as your host user:
   ```yaml
   user: "current"
   ```

3. If the provider needs to write files, set `read_only: false`:
   ```yaml
   read_only: false
   ```

### Tool invocation times out

Increase the timeout:

```python
registry_invoke(
    provider="slow_provider",
    tool="long_operation",
    arguments={...},
    timeout=120.0  # 2 minutes
)
```

Or adjust the default in your configuration.

### How do I check provider status?

Use `registry_list` to see all providers and their states:

```python
registry_list()
# Returns: {"providers": [{"provider_id": "math", "state": "ready", ...}]}
```

Or `registry_details` for detailed information about a specific provider:

```python
registry_details(provider="math")
```

---

## Security

### Is it safe to run untrusted MCP providers?

Use container mode with security restrictions:

```yaml
providers:
  untrusted:
    mode: container
    image: untrusted-mcp:latest
    network: none
    read_only: true
    resources:
      memory: 256m
      cpu: "0.5"
```

This provides:
- Network isolation
- Read-only filesystem
- Resource limits
- Dropped capabilities

### How do I prevent sensitive data from appearing in logs?

MCP Hangar automatically masks sensitive environment variables (passwords, API keys, tokens). Use the `SecureEnvironment` class for additional protection.

### Are there rate limits?

Yes. Configure rate limiting via environment variables:

```bash
export MCP_RATE_LIMIT_RPS=10
export MCP_RATE_LIMIT_BURST=20
```

---

## Performance

### How many providers can MCP Hangar manage?

There's no hard limit. Performance depends on:
- Available system resources
- Provider resource usage
- Invocation frequency

### Why is my provider slow to start?

- **Container mode**: Image pull/build time on first start
- **Subprocess mode**: Python import time, dependency loading

Consider pre-warming providers with `registry_start` if startup time is critical.

### How do I reduce memory usage?

1. Reduce `idle_ttl_s` to shut down unused providers faster
2. Set memory limits for container providers
3. Use fewer concurrent providers

---

## Development

### How do I create a custom MCP provider?

See the `examples/provider_math/` directory for a reference implementation. Your provider must:

1. Implement the MCP JSON-RPC protocol
2. Communicate via stdin/stdout
3. Respond to `initialize`, `tools/list`, and `tools/call` methods

### How do I run tests?

```bash
# All tests
pytest tests/ -v

# Quick tests only
pytest tests/ -v -m "not slow"

# With coverage
pytest tests/ --cov=mcp_hangar --cov-report=html
```

### How do I contribute?

See [CONTRIBUTING.md](development/CONTRIBUTING.md) for development setup, code style guidelines, and the PR process.

---

## Getting Help

- **Documentation**: [docs/INDEX.md](INDEX.md)
- **Issues**: [GitHub Issues](https://github.com/mapyr/mcp-hangar/issues)
- **Security**: See [SECURITY.md](../SECURITY.md) for vulnerability reporting
