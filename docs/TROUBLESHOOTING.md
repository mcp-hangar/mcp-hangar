# Troubleshooting Guide

This guide covers common issues you may encounter when using MCP Hangar and their solutions.

## Quick Diagnostics

Before diving into specific issues, run these checks:

```bash
# Check Python version (requires 3.10+)
python --version

# Verify MCP Hangar installation
python -c "import mcp_hangar; print('OK')"

# Check if config file exists
ls -la config.yaml

# Test a provider directly
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python tests/mock_provider.py
```

## Startup Issues

### "ModuleNotFoundError: No module named 'mcp_hangar'"

**Cause:** Package not installed or virtual environment not activated.

**Solution:**
```bash
# Activate virtual environment
source venv/bin/activate  # Linux/macOS
venv\Scripts\activate     # Windows

# Install package
pip install .
# or
uv pip install .
```

### "FileNotFoundError: config.yaml"

**Cause:** Configuration file not found in current directory.

**Solution:**
```bash
# Option 1: Create config file
cp config.yaml.example config.yaml

# Option 2: Specify path via environment variable
export MCP_CONFIG=/path/to/your/config.yaml
python -m mcp_hangar.server
```

### "ModuleNotFoundError: No module named 'mcp'"

**Cause:** MCP library not installed.

**Solution:**
```bash
pip install mcp
```

## Provider Issues

### Provider doesn't start / "EOF on stdout"

**Cause:** The provider command is not producing valid JSON-RPC output.

**Solutions:**

1. Test the provider directly:
   ```bash
   echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python your_provider.py
   ```

2. Use the mock provider for testing:
   ```yaml
   providers:
     test:
       mode: subprocess
       command:
         - python
         - tests/mock_provider.py
   ```

3. Check if the provider script has execution permissions:
   ```bash
   chmod +x your_provider.py
   ```

### "ProviderDegradedError" / Provider in backoff

**Cause:** Provider failed multiple times and circuit breaker is active.

**Solutions:**

1. Wait for backoff to expire (check `time_until_retry` in `registry_details`)

2. Create a new ProviderManager instance

3. Check provider logs for the root cause of failures

4. Increase `max_consecutive_failures` in config if failures are transient:
   ```yaml
   providers:
     my_provider:
       max_consecutive_failures: 5
   ```

### "ProviderNotFoundError: unknown_provider"

**Cause:** Provider ID not in configuration.

**Solution:**
```bash
# List configured providers
python -c "import yaml; print(yaml.safe_load(open('config.yaml'))['providers'].keys())"

# Verify provider ID matches exactly (case-sensitive)
```

### Provider starts but tools not discovered

**Cause:** Provider doesn't implement `tools/list` correctly.

**Solutions:**

1. Test tools discovery directly:
   ```bash
   echo '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | python your_provider.py
   ```

2. Check that the provider returns valid tool schemas with `name`, `description`, and `inputSchema`

## Container Issues

### "No container runtime found"

**Cause:** Neither Docker nor Podman is installed.

**Solution:**
```bash
# macOS
brew install podman

# Ubuntu/Debian
sudo apt install podman

# Fedora/RHEL
sudo dnf install podman

# Verify installation
podman --version
# or
docker --version
```

### Container build fails

**Cause:** Dockerfile error or missing context.

**Solutions:**

1. Verify Dockerfile exists:
   ```bash
   ls -la docker/Dockerfile.your_provider
   ```

2. Test build manually:
   ```bash
   podman build -t test-image -f docker/Dockerfile.your_provider .
   ```

3. Check build context contains required files

### Container won't start

**Cause:** Container crashes or exits immediately.

**Solutions:**

1. Check container logs:
   ```bash
   podman logs <container_id>
   ```

2. Run container interactively:
   ```bash
   podman run -it --rm your-image:latest /bin/sh
   ```

3. Verify image exists:
   ```bash
   podman images | grep your-image
   ```

### "Permission denied" on volume mount

**Cause:** SELinux or permission issues with mounted paths.

**Solutions:**

1. For SELinux (Fedora/RHEL):
   ```yaml
   volumes:
     - "/host/path:/container/path:ro,Z"
   ```

2. Use current user mapping:
   ```yaml
   providers:
     my_provider:
       user: "current"
   ```

3. Check host directory permissions:
   ```bash
   ls -la /host/path
   ```

### "Permission denied" when writing inside container

**Cause:** Container has read-only filesystem.

**Solution:**
```yaml
providers:
  my_provider:
    read_only: false
    volumes:
      - "./data:/app/data:rw"
```

### Container can't access network

**Cause:** Network isolation is enabled (default).

**Solution:**
```yaml
providers:
  my_provider:
    network: bridge  # or "host" for full access
```

### Pre-built image not found

**Cause:** Image not pulled or registry requires authentication.

**Solutions:**

1. Pull image manually:
   ```bash
   podman pull ghcr.io/org/image:latest
   ```

2. Login to private registry:
   ```bash
   podman login registry.example.com
   ```

## HTTP Mode Issues

### Server doesn't respond on expected port

**Cause:** Wrong port or host configuration.

**Solutions:**

1. Check environment variables:
   ```bash
   export MCP_HTTP_HOST=0.0.0.0
   export MCP_HTTP_PORT=8000
   ```

2. Verify server is running:
   ```bash
   curl http://localhost:8000/mcp
   ```

3. Check for port conflicts:
   ```bash
   lsof -i :8000
   ```

### "Connection refused" from LM Studio

**Cause:** Server not running or firewall blocking.

**Solutions:**

1. Start server in HTTP mode:
   ```bash
   python -m mcp_hangar.server --http
   ```

2. Verify connectivity:
   ```bash
   curl -X POST http://localhost:8000/mcp \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
   ```

3. Check LM Studio config points to correct URL

## Tool Invocation Issues

### "ToolNotFoundError"

**Cause:** Tool name doesn't exist on provider.

**Solutions:**

1. List available tools:
   ```python
   result = registry_tools(provider="my_provider")
   print(result["tools"])
   ```

2. Check tool name spelling (case-sensitive)

### "ToolInvocationError"

**Cause:** Tool execution failed within the provider.

**Solutions:**

1. Check the error message for details

2. Verify arguments match the tool's input schema:
   ```python
   tools = registry_tools(provider="my_provider")
   for tool in tools["tools"]:
       if tool["name"] == "your_tool":
           print(tool["inputSchema"])
   ```

3. Test with minimal arguments first

### "ToolTimeoutError"

**Cause:** Tool execution took longer than timeout.

**Solutions:**

1. Increase timeout:
   ```python
   result = registry_invoke(
       provider="my_provider",
       tool="slow_tool",
       arguments={...},
       timeout=120.0  # 2 minutes
   )
   ```

2. Check if provider is stuck (use `registry_health`)

### "RateLimitExceeded"

**Cause:** Too many requests in a short period.

**Solutions:**

1. Wait and retry after `retry_after` seconds

2. Adjust rate limits via environment:
   ```bash
   export MCP_RATE_LIMIT_RPS=20
   export MCP_RATE_LIMIT_BURST=40
   ```

## Testing Issues

### Tests fail with import errors

**Cause:** Dev dependencies not installed.

**Solution:**
```bash
pip install .[dev]
```

### Docker tests fail

**Cause:** Container images not built.

**Solution:**
```bash
podman build -t mcp-math:latest -f docker/Dockerfile.math .
podman build -t mcp-memory:latest -f docker/Dockerfile.memory .
podman build -t mcp-filesystem:latest -f docker/Dockerfile.filesystem .
podman build -t mcp-fetch:latest -f docker/Dockerfile.fetch .
```

### Tests hang indefinitely

**Cause:** Provider process not terminating.

**Solutions:**

1. Run tests with timeout:
   ```bash
   pytest tests/ -v --timeout=60
   ```

2. Check for zombie processes:
   ```bash
   ps aux | grep python
   ```

## Performance Issues

### Provider slow to start

**Cause:** Cold start overhead.

**Solutions:**

1. Keep providers warm by adjusting TTL:
   ```yaml
   providers:
     my_provider:
       idle_ttl_s: 600  # 10 minutes
   ```

2. Pre-start providers:
   ```python
   registry_start(provider="my_provider")
   ```

### High memory usage

**Cause:** Too many providers running or memory leaks.

**Solutions:**

1. Reduce idle TTL to clean up sooner:
   ```yaml
   providers:
     my_provider:
       idle_ttl_s: 60
   ```

2. Set container memory limits:
   ```yaml
   providers:
     my_provider:
       resources:
         memory: 256m
   ```

3. Monitor with `registry_health`

## Logging and Debugging

### Enable debug logging

```bash
export LOG_LEVEL=DEBUG
python -m mcp_hangar.server
```

### View structured logs

Logs are JSON-formatted. Use `jq` for readability:
```bash
python -m mcp_hangar.server 2>&1 | jq .
```

### Trace specific provider

Check provider details:
```python
details = registry_details(provider="my_provider")
print(f"State: {details['state']}")
print(f"Health: {details['health']}")
print(f"Idle time: {details['idle_time']}s")
```

## Getting Help

If you're still stuck:

1. **Search existing issues:** [GitHub Issues](https://github.com/mapyr/mcp-hangar/issues)
2. **Check the FAQ:** [FAQ](FAQ.md)
3. **Open a new issue** with:
   - MCP Hangar version
   - Python version
   - Operating system
   - Full error message and stack trace
   - Minimal reproduction steps
   - Relevant configuration (without secrets)
