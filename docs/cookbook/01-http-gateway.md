# 01 — HTTP Gateway

> **Prerequisite:** None
> **You will need:** A running Streamable HTTP MCP server (test server provided below)
> **Time:** 5 minutes
> **Adds:** Single remote MCP server behind Hangar as control plane

## The Problem

You have one MCP server today. Tomorrow you'll have five. You need a control plane before that happens. Right now, Claude Desktop connects directly to your MCP server — no visibility, no lifecycle management, no single point of configuration. When you add the second server, you're managing two configs. By the fifth, it's chaos.

## Prerequisites

You need a running MCP server to point Hangar at. Use any Streamable HTTP MCP server you already have, or start a test one:

```bash
# Option A: Using npx (Node.js)
npx -y @anthropic/mcp-server-everything --transport sse --port 8080
```

```bash
# Option B: Using uvx (Python)
uvx mcp-server-fetch --transport sse --port 8080
```

Keep this running in a separate terminal.

## The Config

```yaml
# config.yaml — Recipe 01: HTTP Gateway
mcp_servers:
  my-mcp:
    mode: remote
    endpoint: http://localhost:8080/sse
    description: "My remote MCP server"
    http:
      connect_timeout: 10.0
      read_timeout: 30.0
```

Save this as `~/.config/mcp-hangar/config.yaml` or pass it with `--config`.

## Try It

1. Test Hangar with the config (using stdin/stdout)

   ```bash
   echo '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":1}' | \
     mcp-hangar --config ~/.config/mcp-hangar/config.yaml serve
   ```

   ```json
   {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05","capabilities":{...},"serverInfo":{"name":"mcp-registry","version":"1.25.0"}}}
   ```

   Hangar responds to MCP initialize. Press Ctrl+C to stop.

2. Check MCP server status (create test script)

   ```bash
   cat > /tmp/test-hangar.sh << 'EOF'
   #!/bin/bash
   (
     echo '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":1}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"hangar_status","arguments":{}},"id":2}'
     sleep 2
   ) | mcp-hangar --config ~/.config/mcp-hangar/config.yaml serve 2>/dev/null | grep '"id":2'
   EOF
   chmod +x /tmp/test-hangar.sh
   /tmp/test-hangar.sh
   ```

   ```json
   {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"...\"id\": \"my-mcp\", \"indicator\": \"[COLD]\", \"state\": \"cold\"..."}]}}
   ```

   MCP Server shows COLD state (not started yet).

3. List tools to trigger cold start

   ```bash
   cat > /tmp/test-list.sh << 'EOF'
   #!/bin/bash
   (
     echo '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":1}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"hangar_list","arguments":{}},"id":2}'
     sleep 5
   ) | mcp-hangar --config ~/.config/mcp-hangar/config.yaml serve 2>/dev/null | grep '"id":2'
   EOF
   chmod +x /tmp/test-list.sh
   /tmp/test-list.sh
   ```

   ```json
   {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"...\"mcp_server\": \"my-mcp\", \"state\": \"ready\", \"mode\": \"subprocess\", \"tools_count\": 2..."}]}}
   ```

   MCP Server transitioned to READY and discovered tools.

4. Configure Claude Desktop

   Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

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

## What Just Happened

Hangar loaded your MCP server configuration and started in stdio mode (JSON-RPC over stdin/stdout). When you sent the `initialize` handshake, Hangar responded with its capabilities. On the first `hangar_list` call, Hangar performed a cold start: it launched the subprocess MCP server (`uvx mcp-server-fetch` in this example, or connects to remote endpoint if using `mode: remote`), sent MCP `initialize` + `tools/list` to discover available tools, and registered them in its internal registry.

The test MCP server doesn't know Hangar exists — it sees standard MCP JSON-RPC requests. This is a transparent proxy pattern. Hangar adds nothing yet: no health checks, no circuit breaker, no authentication. That's the point — recipe 01 is the baseline.

## Key Config Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mode` | string | — | MCP Server mode. Use `remote` for HTTP/SSE MCP servers |
| `endpoint` | string | — | Full URL of the remote MCP server (including path) |
| `description` | string | `""` | Human-readable description shown in status |
| `http.connect_timeout` | float | `10.0` | TCP connection timeout in seconds |
| `http.read_timeout` | float | `30.0` | Response read timeout in seconds |

## What's Next

Your MCP server is proxied — but what happens when it goes down? Right now, Hangar sends requests into the void and forwards the error. You need visibility into MCP server health before failures surprise you.

→ [02 — Health Checks](02-health-checks.md)
