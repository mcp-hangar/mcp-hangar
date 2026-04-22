# 04 — Failover

> **Prerequisite:** [03 — Circuit Breaker](03-circuit-breaker.md)
> **You will need:** Two running MCP servers (primary + backup)
> **Time:** 15 minutes
> **Adds:** Automatic failover to backup MCP server with priority-based routing

## The Problem

Circuit breaker from recipe 03 saved you from wasting 30 seconds per failed call. But the agent still got nothing. Zero results. The circuit opened, requests failed fast, and your agent couldn't complete its task. Protection is great, but errors are still errors.

Your single MCP server is one crash away from downtime. What if there was a second MCP server ready to answer while the primary recovers?

## Prerequisites

You need TWO running MCP servers. Start both:

```bash
# Terminal 1: Primary server on port 8080
uvx mcp-server-fetch &

# Terminal 2: Backup server on port 8081
# (simulate by running another instance - in real world this would be a different host)
MCP_PORT=8081 uvx mcp-server-fetch &
```

Keep both running.

## The Config

```yaml
# config.yaml — Recipe 04: Failover

health_check:
  enabled: true
  interval_s: 30

mcp_servers:
  my-mcp:
    mode: remote
    endpoint: http://localhost:8080/sse
    description: "Primary MCP server"
    health_check_interval_s: 30
    max_consecutive_failures: 3
    http:
      connect_timeout: 10.0
      read_timeout: 30.0

  my-mcp-backup:                           # NEW: added in this recipe
    mode: remote                           # NEW: added in this recipe
    endpoint: http://localhost:8081/sse    # NEW: added in this recipe
    description: "Backup MCP server"       # NEW: added in this recipe
    health_check_interval_s: 30            # NEW: added in this recipe
    max_consecutive_failures: 3            # NEW: added in this recipe
    http:                                  # NEW: added in this recipe
      connect_timeout: 10.0                # NEW: added in this recipe
      read_timeout: 30.0                   # NEW: added in this recipe

  my-mcp-group:
    mode: group
    description: "Primary/backup MCP failover"
    strategy: priority                     # NEW: changed from round_robin
    min_healthy: 1
    circuit_breaker:
      failure_threshold: 3
      reset_timeout_s: 30
    members:
      - id: my-mcp                         # NEW: added priority
        priority: 1                        # NEW: added priority (primary)
      - id: my-mcp-backup                  # NEW: added backup member
        priority: 2                        # NEW: backup has lower priority
```

Save this as `~/.config/mcp-hangar/config.yaml` (or update your existing file).

## Try It

1. Start Hangar with the new config

   ```bash
   mcp-hangar --config ~/.config/mcp-hangar/config.yaml serve \
     --log-file /tmp/hangar-failover.log &
   ```

   ```
   INFO     group_created group_id=my-mcp-group strategy=priority
   INFO     Added member my-mcp to group my-mcp-group (priority=1)
   INFO     Added member my-mcp-backup to group my-mcp-group (priority=2)
   ```

2. Check group status - both members healthy

   ```bash
   tail -20 /tmp/hangar-failover.log | grep -E "member|health|rotation"
   ```

   ```
   INFO     member_added_to_rotation member=my-mcp group=my-mcp-group
   INFO     member_added_to_rotation member=my-mcp-backup group=my-mcp-group
   INFO     group_state_changed group=my-mcp-group state=healthy members=2/2
   ```

   Both MCP servers in rotation. Primary (priority 1) will handle requests.

3. Call a tool through the group - succeeds via primary

   ```bash
   (
     echo '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":1}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"hangar_call","arguments":{"mcp_server":"my-mcp-group","tool":"fetch","arguments":{"url":"https://example.com"}}},"id":2}'
     sleep 3
   ) | mcp-hangar --config ~/.config/mcp-hangar/config.yaml serve 2>&1 | grep -E '"id":2|selected_member'
   ```

   ```json
   {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"..."}]}}
   ```

   Call succeeded. Traffic routed to primary (priority 1).

4. Kill the primary server

   ```bash
   pkill -f "mcp-server-fetch.*8080"
   ```

   Primary is now dead. Backup still running.

5. Wait for health check to detect failure

   ```bash
   echo "Waiting 40 seconds for health detection..."
   sleep 40
   tail -10 /tmp/hangar-failover.log | grep -E "health|rotation|degraded"
   ```

   ```
   WARNING  health_check_failed mcp_server=my-mcp consecutive_failures=1
   WARNING  health_check_failed mcp_server=my-mcp consecutive_failures=2
   WARNING  health_check_failed mcp_server=my-mcp consecutive_failures=3
   INFO     member_removed_from_rotation member=my-mcp reason=health_check_failures
   ```

   Primary out of rotation. Backup takes over.

6. Call the same tool - succeeds via backup

   ```bash
   (
     echo '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":1}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"hangar_call","arguments":{"mcp_server":"my-mcp-group","tool":"fetch","arguments":{"url":"https://example.com"}}},"id":2}'
     sleep 3
   ) | mcp-hangar --config ~/.config/mcp-hangar/config.yaml serve 2>&1 | grep -E '"id":2|selected_member'
   ```

   ```json
   {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"..."}]}}
   ```

   Call succeeded. Same request, same result, different MCP server. Zero downtime.

7. Restart primary and verify failback

   ```bash
   # Restart primary
   uvx mcp-server-fetch &

   # Wait for recovery
   echo "Waiting 40 seconds for primary recovery..."
   sleep 40

   # Check logs
   tail -10 /tmp/hangar-failover.log | grep -E "health|rotation|ready"
   ```

   ```
   INFO     health_check_passed mcp_server=my-mcp
   INFO     member_added_to_rotation member=my-mcp reason=health_recovered
   INFO     group_state_changed group=my-mcp-group state=healthy members=2/2
   ```

   Primary recovered and back in rotation. Will reclaim traffic (priority 1 < priority 2).

## What Just Happened

You introduced **MCP server groups with priority-based routing** for automatic failover. The group contains two MCP servers: `my-mcp` (priority 1, primary) and `my-mcp-backup` (priority 2, backup).

**Priority strategy mechanics:**

The `priority` load balancing strategy always routes traffic to the lowest-numbered healthy member in rotation. Priority 1 is highest priority (primary). If priority 1 becomes unhealthy, traffic automatically fails over to priority 2 (backup). When priority 1 recovers, it reclaims traffic (failback).

**Failover flow:**

1. **Normal operation**: Primary (priority 1) handles all requests. Backup is healthy but idle.
2. **Primary fails**: Health checks detect failure after 3 consecutive misses (~90 seconds).
3. **Failover**: Primary removed from rotation. Group selects next lowest priority → backup (priority 2) takes over.
4. **Recovery**: Primary health checks succeed. Primary added back to rotation.
5. **Failback**: Group selects lowest priority again → primary (priority 1) reclaims traffic.

**Layer cake architecture:**

- **Recipe 02 (Health Checks)**: Per-MCP server health monitoring detects failures
- **Recipe 03 (Circuit Breaker)**: Per-group fast-fail protection
- **Recipe 04 (Failover)**: Inter-MCP server routing changes based on health

Both MCP servers have their own health checks and circuit breakers. The group orchestrates between them. When the primary fails, its circuit may open AND health checks fail AND the group removes it from rotation. Multiple layers of protection working together.

**min_healthy: 1** means the group requires at least 1 healthy member to stay operational. If both fail, the group itself becomes unavailable.

## Key Config Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `MCP servers.<name>.strategy` | string | — | Routing strategy. Use `priority` for failover |
| `MCP servers.<name>.members[].id` | string | — | MCP Server ID (must exist in `MCP servers:` section) |
| `MCP servers.<name>.members[].priority` | int | `1` | Routing priority (lower number = higher priority) |
| `MCP servers.<name>.members[].weight` | int | `1` | Weight for weighted strategies (not used with priority) |

## What's Next

You have failover — one primary, one backup. But what if you have three, five, ten instances of the same MCP server? You don't want priority failover — you want to spread the load evenly across all healthy instances.

→ [05 — Load Balancing](05-load-balancing.md)
