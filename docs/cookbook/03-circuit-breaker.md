# 03 — Circuit Breaker

> **Prerequisite:** [02 — Health Checks](02-health-checks.md)
> **You will need:** Working setup from recipe 02
> **Time:** 15 minutes
> **Adds:** MCP Server groups with circuit breaker for fast-fail protection

## The Problem

Health checks from recipe 02 run every 30 seconds. Between checks, a flaky MCP server can accept requests, fail, get retried, fail again — wasting agent time and tokens on a MCP server that's clearly broken. Your MCP server responds to health checks (it's technically alive) but fails 80% of real tool calls. Intermittent failure. Health checks say READY. Agents suffer.

Health checks tell you the patient is dead. Circuit breakers stop you from performing surgery on a corpse.

## The Config

```yaml
# config.yaml — Recipe 03: Circuit Breaker

health_check:
  enabled: true
  interval_s: 30

mcp_servers:
  my-mcp:
    mode: remote
    endpoint: http://localhost:8080/sse
    description: "My remote MCP server"
    health_check_interval_s: 30
    max_consecutive_failures: 3
    http:
      connect_timeout: 10.0
      read_timeout: 30.0

  my-mcp-group:                            # NEW: added in this recipe
    mode: group                            # NEW: added in this recipe
    description: "My MCP group with circuit breaker"  # NEW: added in this recipe
    strategy: round_robin                  # NEW: added in this recipe
    min_healthy: 1                         # NEW: added in this recipe
    circuit_breaker:                       # NEW: added in this recipe
      failure_threshold: 3                 # NEW: added in this recipe
      reset_timeout_s: 30                  # NEW: added in this recipe
    members:                               # NEW: added in this recipe
      - id: my-mcp                         # NEW: added in this recipe
```

Save this as `~/.config/mcp-hangar/config.yaml` (or update your existing file).

## Try It

1. Start Hangar with the new config

   ```bash
   mcp-hangar --config ~/.config/mcp-hangar/config.yaml serve \
     --log-file /tmp/hangar-circuit.log &
   ```

   ```
   INFO     group_created group_id=my-mcp-group strategy=round_robin
   INFO     background_worker_started task=health_check interval_s=60
   ```

2. Call a tool successfully through the group

   ```bash
   (
     echo '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":1}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"hangar_call","arguments":{"mcp_server":"my-mcp-group","tool":"fetch","arguments":{"url":"https://example.com"}}},"id":2}'
     sleep 3
   ) | mcp-hangar --config ~/.config/mcp-hangar/config.yaml serve 2>&1 | grep -E '"id":2|circuit'
   ```

   ```json
   {"jsonrpc":"2.0","id":2,"result":{"content":[...]}}
   ```

   Circuit breaker is CLOSED (normal operation). Call succeeded.

3. Kill the MCP server to simulate failures

   ```bash
   ps aux | grep mcp-server | grep -v grep
   kill <PID>
   ```

   MCP Server is now dead.

4. Call the tool 3 times to trip the circuit

   ```bash
   for i in 1 2 3; do
     echo "Attempt $i..."
     (
       echo '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":1}'
       sleep 0.5
       echo '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
       sleep 0.5
       echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"hangar_call","arguments":{"mcp_server":"my-mcp-group","tool":"fetch","arguments":{"url":"https://example.com"}}},"id":2}'
       sleep 3
     ) | mcp-hangar --config ~/.config/mcp-hangar/config.yaml serve 2>&1 | grep -E 'error|circuit' | head -2
   done
   ```

   ```
   Attempt 1...
   WARNING  tool_call_failed mcp_server=my-mcp-group error=Connection refused
   Attempt 2...
   WARNING  tool_call_failed mcp_server=my-mcp-group error=Connection refused
   Attempt 3...
   WARNING  circuit_breaker_opened group=my-mcp-group failures=3
   ```

   After 3 failures, circuit opens.

5. Verify fast-fail behavior — the key demonstration

   ```bash
   time (
     echo '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":1}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"hangar_call","arguments":{"mcp_server":"my-mcp-group","tool":"fetch","arguments":{"url":"https://example.com"}}},"id":2}'
     sleep 1
   ) | mcp-hangar --config ~/.config/mcp-hangar/config.yaml serve 2>&1 | grep -E 'circuit_open|rejected'
   ```

   ```
   ERROR    call_rejected_circuit_open group=my-mcp-group

   real    0m2.1s
   ```

   Request rejected in ~2 seconds (no 30-second timeout). This is the protection.

6. Wait for circuit to auto-reset

   ```bash
   echo "Waiting 35 seconds for circuit reset..."
   sleep 35
   tail -5 /tmp/hangar-circuit.log
   ```

   ```
   INFO     circuit_reset_timeout_elapsed group=my-mcp-group
   ```

   Circuit automatically transitions from OPEN to CLOSED after `reset_timeout_s`.

7. Restart MCP server and verify recovery

   ```bash
   uvx mcp-server-fetch &
   sleep 2

   (
     echo '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":1}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"hangar_call","arguments":{"mcp_server":"my-mcp-group","tool":"fetch","arguments":{"url":"https://example.com"}}},"id":2}'
     sleep 3
   ) | mcp-hangar --config ~/.config/mcp-hangar/config.yaml serve 2>&1 | grep -E '"id":2|success'
   ```

   ```json
   {"jsonrpc":"2.0","id":2,"result":{"content":[...]}}
   ```

   Call succeeded. Circuit is CLOSED. Full recovery.

## What Just Happened

Hangar introduced **MCP server groups** — a logical grouping of one or more MCP servers with shared policies. The group has a circuit breaker that tracks real tool call failures, not synthetic health probes.

**Circuit breaker states:**

**CLOSED** (normal operation): All calls pass through to group members. The circuit breaker counts consecutive failures. When `failure_count` reaches `failure_threshold` (3), the circuit opens.

**OPEN** (protecting): All calls are rejected immediately with a circuit-open error. No traffic reaches the MCP server — this is the protection. Instead of waiting 10+ seconds for connection timeout, Hangar fails in milliseconds. After `reset_timeout_s` (30 seconds), the circuit automatically closes and allows traffic again.

**How this differs from health checks:**

- **Health checks** (recipe 02): Periodic synthetic probe (`tools/list` every 30s). Detects "is the MCP server alive?"
- **Circuit breaker** (recipe 03): Tracks real tool call failures in real-time. Detects "is the MCP server working?"

They complement each other. Health checks catch dead MCP servers. Circuit breakers catch flaky MCP servers that pass health checks but fail real requests. The circuit breaker trips instantly on the Nth failure — no waiting for the next health check cycle.

## Key Config Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `MCP servers.<name>.mode` | string | — | Set to `group` for MCP server groups |
| `MCP servers.<name>.strategy` | string | `round_robin` | Load balancing strategy |
| `MCP servers.<name>.min_healthy` | int | `1` | Minimum healthy members required |
| `MCP servers.<name>.circuit_breaker.failure_threshold` | int | `10` | Consecutive failures before circuit opens |
| `MCP servers.<name>.circuit_breaker.reset_timeout_s` | float | `60.0` | Seconds before circuit auto-closes |
| `MCP servers.<name>.members` | list | — | List of MCP server IDs or inline definitions |

## What's Next

Your single MCP server is protected — but it's still a single point of failure. When the circuit opens, agents get errors instead of results. What if there was a backup MCP server ready to take over automatically?

→ [04 — Failover](04-failover.md)
