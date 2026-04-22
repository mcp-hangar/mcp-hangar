# 08 -- Observability: Langfuse

> **Prerequisite:** [01 -- HTTP Gateway](01-http-gateway.md)
> **You will need:** Running Hangar, Langfuse instance (cloud or self-hosted)
> **Time:** 10 minutes
> **Adds:** Distributed tracing for tool invocations via Langfuse

## The Problem

You know a tool call was slow. You don't know whether the delay was in Hangar (routing, cold start) or in the MCP server itself. You need end-to-end traces that break down each phase.

## The Config

```yaml
# config.yaml -- Recipe 08: Langfuse Tracing
mcp_servers:
  my-mcp:
    mode: remote
    endpoint: "http://localhost:8080"
    health_check_interval_s: 10
    max_consecutive_failures: 3

observability:                           # NEW: Langfuse tracing
  langfuse:                              # NEW: Langfuse configuration
    enabled: true                        # NEW: enable Langfuse adapter
    public_key: ${LANGFUSE_PUBLIC_KEY}   # NEW: from environment
    secret_key: ${LANGFUSE_SECRET_KEY}   # NEW: from environment
    host: "https://cloud.langfuse.com"   # NEW: Langfuse host
```

## Try It

1. Set environment variables:

   ```bash
   export LANGFUSE_PUBLIC_KEY="pk-lf-..."
   export LANGFUSE_SECRET_KEY="sk-lf-..."
   ```

2. Start Hangar:

   ```bash
   mcp-hangar serve --http --port 8000
   ```

3. Make a tool call:

   ```bash
   curl -X POST http://localhost:8000/api/mcp_servers/my-mcp/start
   ```

4. Open Langfuse dashboard and find the trace. You see spans for:
   - `hangar.tool_invocation` -- overall call
   - `hangar.cold_start` -- MCP server initialization (if cold)
   - `hangar.mcp_server_call` -- actual MCP server communication

## What Just Happened

The `TracedProviderService` wraps tool invocations with Langfuse spans via the `LangfuseObservabilityAdapter`. Each tool call creates a trace with child spans for cold start (if needed) and the actual MCP server call. Correlation IDs link Hangar traces to MCP server-side traces.

## Key Config Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `observability.langfuse.enabled` | bool | `false` | Enable Langfuse tracing |
| `observability.langfuse.public_key` | string | -- | Langfuse public key (use env var) |
| `observability.langfuse.secret_key` | string | -- | Langfuse secret key (use env var) |
| `observability.langfuse.host` | string | `https://cloud.langfuse.com` | Langfuse host URL |

## What's Next

You've set up external observability. Now try running MCP servers as local subprocesses instead of remote HTTP.

--> [09 -- Subprocess MCP servers](09-subprocess-MCP servers.md)
