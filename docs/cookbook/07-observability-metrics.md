# 07 -- Observability: Metrics

> **Prerequisite:** [01 -- HTTP Gateway](01-http-gateway.md)
> **You will need:** Running Hangar in HTTP mode, Docker for monitoring stack
> **Time:** 10 minutes
> **Adds:** Prometheus metrics and Grafana dashboards

## The Problem

You have providers running. You don't know how many tool calls they handle, how long calls take, or whether health checks are passing. When something breaks at 3 AM, you need data, not guesses.

## The Config

```yaml
# config.yaml -- Recipe 07: Observability Metrics
providers:
  my-mcp:
    mode: remote
    endpoint: "http://localhost:8080"
    health_check_interval_s: 10
    max_consecutive_failures: 3
```

No config changes needed -- metrics are always available at `/metrics` on the HTTP server.

## Try It

1. Start Hangar in HTTP mode:

   ```bash
   mcp-hangar serve --http --port 8000
   ```

2. Check Prometheus metrics are exposed:

   ```bash
   curl -s http://localhost:8000/metrics | head -20
   ```

   ```
   # HELP mcp_hangar_tool_calls_total Total tool invocations
   # TYPE mcp_hangar_tool_calls_total counter
   mcp_hangar_tool_calls_total{provider="my-mcp",tool="my-tool"} 0
   # HELP mcp_hangar_provider_state Current provider state
   # TYPE mcp_hangar_provider_state gauge
   ```

3. Start the monitoring stack:

   ```bash
   cd monitoring
   docker compose up -d
   ```

4. Open Grafana at `http://localhost:3000` (admin/admin) and check the MCP Hangar dashboard.

5. Make some tool calls and watch the metrics update in real time:

   ```bash
   curl -X POST http://localhost:8000/api/providers/my-mcp/start
   ```

## What Just Happened

Hangar exposes Prometheus-format metrics at `/metrics`. The monitoring stack in `monitoring/` includes pre-configured Prometheus scraping and Grafana dashboards. Key metrics:

| Metric | Type | What it tells you |
|--------|------|-------------------|
| `mcp_hangar_tool_calls_total` | Counter | Total tool invocations per provider/tool |
| `mcp_hangar_tool_call_duration_seconds` | Histogram | Latency distribution per provider/tool |
| `mcp_hangar_provider_state` | Gauge | Current state per provider (1 = active) |
| `mcp_hangar_cold_starts_total` | Counter | Cold start count per provider/mode |
| `mcp_hangar_health_checks` | Counter | Health check results per provider |
| `mcp_hangar_circuit_breaker_state` | Gauge | Circuit breaker state per provider |

## Key Config Reference

No new config keys. Metrics are always available in HTTP mode.

| Endpoint | Description |
|----------|-------------|
| `/metrics` | Prometheus text format |
| `/api/observability/metrics` | JSON summary + Prometheus text |
| `/api/observability/metrics/history` | Time-series snapshots for charts |

## What's Next

Metrics tell you what happened. Traces tell you why.

--> [08 -- Observability: Langfuse](08-observability-langfuse.md)
