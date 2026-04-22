# 09 -- Subprocess MCP servers

> **Prerequisite:** [01 -- HTTP Gateway](01-http-gateway.md)
> **You will need:** Running Hangar, Python 3.11+
> **Time:** 5 minutes
> **Adds:** Run MCP servers as local subprocesses via stdin/stdout

## The Problem

You have a Python MCP server package. You don't want to run it as a separate HTTP service -- you want Hangar to manage its lifecycle directly. Start it on demand, stop it when idle.

## The Config

```yaml
# config.yaml -- Recipe 09: Subprocess MCP servers
mcp_servers:
  math:
    mode: subprocess                     # NEW: subprocess mode
    command: [python, -m, math_server]   # NEW: command to run
    idle_ttl_s: 300                      # NEW: stop after 5min idle
    health_check_interval_s: 60
    max_consecutive_failures: 3
    env:                                 # NEW: environment variables
      PYTHONUNBUFFERED: "1"
```

## Try It

1. Start Hangar:

   ```bash
   mcp-hangar serve
   ```

2. Check status -- MCP server is COLD (not yet started):

   ```bash
   mcp-hangar status
   ```

   ```
   math    subprocess    cold    tools=0    idle
   ```

3. Invoke a tool -- this triggers a cold start:

   ```bash
   mcp-hangar call math add '{"a": 1, "b": 2}'
   ```

   ```
   Result: 3
   ```

4. Check status again -- MCP server is now READY:

   ```bash
   mcp-hangar status
   ```

   ```
   math    subprocess    ready    tools=5    idle_ttl=300s
   ```

5. Wait 5 minutes (or set `idle_ttl_s: 10` for testing) and watch it stop:

   ```bash
   mcp-hangar status
   ```

   ```
   math    subprocess    cold    tools=5    idle
   ```

## What Just Happened

Subprocess MCP servers communicate via JSON-RPC over stdin/stdout. Hangar starts the process on first tool call, keeps it running while active, and stops it after the idle TTL expires. The `StdioClient` manages message correlation, timeouts, and process lifecycle.

Stderr output is captured into a ring buffer and available via the [Log Streaming](../guides/LOG_STREAMING.md) API.

## Key Config Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mode` | string | -- | Set to `subprocess` |
| `command` | list[string] | -- | Command and arguments to start the MCP server |
| `idle_ttl_s` | int | `300` | Seconds of inactivity before auto-stop |
| `env` | dict | `{}` | Environment variables for the subprocess |

## What's Next

Subprocesses are great for development. For isolation in production, run MCP servers in containers.

--> [10 -- Discovery: Docker](10-discovery-docker.md)
