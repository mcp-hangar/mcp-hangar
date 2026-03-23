# 02 — Health Checks

> **Prerequisite:** [01 — HTTP Gateway](01-http-gateway.md)
> **You will need:** Working setup from recipe 01, ability to kill the test MCP server
> **Time:** 10 minutes
> **Adds:** Automatic health monitoring with state transitions on failure

## The Problem

Your provider from recipe 01 crashes at 3 AM. Hangar doesn't know. It keeps sending requests to a dead endpoint and forwards cryptic connection errors back to Claude. Claude retries. More errors. The on-call engineer gets paged because "AI is broken" — but nobody knows which provider is down until someone checks logs. Hangar could have told you in 30 seconds.

## The Config

```yaml
# config.yaml — Recipe 02: Health Checks

health_check:                              # NEW: added in this recipe
  enabled: true                            # NEW: added in this recipe
  interval_s: 30                           # NEW: added in this recipe

providers:
  my-mcp:
    mode: remote
    endpoint: http://localhost:8080/sse
    description: "My remote MCP server"
    health_check_interval_s: 30            # NEW: added in this recipe
    max_consecutive_failures: 3            # NEW: added in this recipe
    http:
      connect_timeout: 10.0
      read_timeout: 30.0
```

Save this as `~/.config/mcp-hangar/config.yaml` (or update your existing file).

## Try It

1. Start Hangar with health checks enabled (background process)

   ```bash
   mcp-hangar --config ~/.config/mcp-hangar/config.yaml serve \
     --log-file /tmp/hangar.log &
   ```

   ```
   INFO     background_worker_started task=health_check interval_s=60
   [1] 12345
   ```

   Note the process ID. Health check worker starts automatically.

2. Check initial status

   ```bash
   cat > /tmp/check-status.sh << 'EOF'
   #!/bin/bash
   (
     echo '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":1}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"hangar_status","arguments":{}},"id":2}'
     sleep 2
   ) | nc localhost 8765 2>/dev/null || echo '{"result":"Use stdio mode or check logs"}'
   EOF
   chmod +x /tmp/check-status.sh

   # Alternative: Check logs
   tail -5 /tmp/hangar.log
   ```

   ```
   INFO     provider_state provider_id=my-mcp state=cold
   INFO     mcp_registry_ready providers=['my-mcp']
   ```

3. Trigger provider start

   ```bash
   (
     echo '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":1}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
     sleep 0.5
     echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"hangar_list","arguments":{}},"id":2}'
     sleep 3
     pkill -HUP -f "mcp-hangar"
   ) | mcp-hangar --config ~/.config/mcp-hangar/config.yaml serve 2>&1 | grep -E "ready|cold"
   ```

   ```
   INFO     provider_state_transition provider_id=my-mcp from=cold to=ready
   ```

   Provider transitioned to READY. Health checks now active.

4. Simulate provider failure

   Kill the subprocess provider directly (if using subprocess mode) or simulate network failure (if using remote mode):

   ```bash
   # Find and kill the subprocess provider
   ps aux | grep mcp-server-fetch | grep -v grep
   kill <PID>
   ```

   Or forcefully fail the provider by breaking its process.

5. Wait for health check cycle (40 seconds)

   ```bash
   echo "Waiting for health checks to detect failure..."
   sleep 40
   tail -10 /tmp/hangar.log | grep -E "health_check|degraded"
   ```

   ```
   WARNING  health_check_failed provider_id=my-mcp error=Connection refused
   WARNING  health_check_failed provider_id=my-mcp error=Connection refused
   WARNING  health_check_failed provider_id=my-mcp error=Connection refused
   WARNING  provider_degraded_by_health_check provider_id=my-mcp
   ```

6. Verify DEGRADED state

   ```bash
   # Check the process still running
   ps aux | grep "mcp-hangar" | grep -v grep

   # Check latest logs
   tail -3 /tmp/hangar.log
   ```

   ```
   INFO     provider_state provider_id=my-mcp state=degraded consecutive_failures=3
   ```

7. Provider will auto-recover if restarted

   For subprocess mode, Hangar will attempt restart on next invocation. For remote mode, restart the remote server manually.

8. Stop Hangar

   ```bash
   pkill -f "mcp-hangar.*serve"
   ```

## What Just Happened

Hangar's background health check worker probes each READY provider every 30 seconds (configured via `health_check.interval_s`). The probe mechanism sends a `tools/list` JSON-RPC request to the provider with a 5-second timeout. If the provider responds successfully, the health check passes and `consecutive_failures` resets to 0.

When the provider fails to respond, Hangar records a failure in the `HealthTracker`. After `max_consecutive_failures` (3 by default) failed checks, the provider state transitions from READY to DEGRADED. This transition emits a `ProviderDegraded` domain event, which updates metrics and triggers alerts.

When the provider comes back online, Hangar reinitializes it (DEGRADED -> INITIALIZING -> READY). No manual intervention required -- Hangar detected the failure and recovery without human involvement.

State machine transitions:

- READY -> DEGRADED (after 3 consecutive failures)
- DEGRADED -> INITIALIZING -> READY (on reinitialize after recovery)

## Key Config Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `health_check.enabled` | bool | `true` | Enable global health checking |
| `health_check.interval_s` | int | `30` | Default interval between checks (seconds) |
| `health_check_interval_s` | int | `60` | Per-provider health check interval override |
| `max_consecutive_failures` | int | `3` | Failures before state transition to DEGRADED |

## What's Next

Your provider is monitored — but when it starts failing intermittently, every request during the 30-second health check interval still hits a broken server. You need automatic protection that trips immediately on the first failure, not after 3 health checks.

→ [03 — Circuit Breaker](03-circuit-breaker.md)
