# 06 -- Rate Limiting

> **Prerequisite:** [05 -- Load Balancing](05-load-balancing.md)
> **You will need:** Running Hangar with a load-balanced group from recipe 05
> **Time:** 5 minutes
> **Adds:** Protect providers from request overload

## The Problem

A runaway client sends hundreds of requests per second. Your providers can handle 10 concurrent calls each. Without limits, they queue up, timeout, and cascade into health check failures.

## The Config

```yaml
# config.yaml -- Recipe 06: Rate Limiting
providers:
  my-mcp:
    mode: remote
    endpoint: "http://localhost:8080"
    health_check_interval_s: 10
    max_consecutive_failures: 3

  my-mcp-backup:
    mode: remote
    endpoint: "http://localhost:8081"
    health_check_interval_s: 10
    max_consecutive_failures: 3

  my-mcp-3:
    mode: remote
    endpoint: "http://localhost:8082"
    health_check_interval_s: 10
    max_consecutive_failures: 3

  my-mcp-group:
    mode: group
    strategy: round_robin
    min_healthy: 1
    members:
      - id: my-mcp
        weight: 1
      - id: my-mcp-backup
        weight: 1
      - id: my-mcp-3
        weight: 1

rate_limiting:                           # NEW: global rate limiting
  enabled: true                          # NEW: enable rate limiter
  max_requests_per_minute: 60            # NEW: 60 requests/min per principal
  burst_size: 10                         # NEW: allow short bursts up to 10
```

## Try It

1. Start Hangar with the updated config:

   ```bash
   mcp-hangar serve --http --port 8000
   ```

2. Send requests within the limit:

   ```bash
   for i in $(seq 1 5); do
     curl -s http://localhost:8000/api/providers | jq .providers[0].state
   done
   ```

   All 5 requests succeed.

3. Flood the API to trigger rate limiting:

   ```bash
   for i in $(seq 1 100); do
     curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/providers
   done
   ```

   After the burst limit, you see `429` responses:

   ```
   200
   200
   ...
   429
   429
   ```

4. Wait 60 seconds and verify the limit resets:

   ```bash
   sleep 60
   curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/providers
   ```

   ```
   200
   ```

## What Just Happened

The `RateLimitMiddleware` in the command bus pipeline tracks requests per principal (or per IP for anonymous requests). When the rate exceeds `max_requests_per_minute`, subsequent requests receive `429 Too Many Requests`. The `burst_size` allows short spikes above the steady-state rate.

Rate limiting applies to both MCP tool calls and REST API requests.

## Key Config Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `rate_limiting.enabled` | bool | `false` | Enable rate limiting |
| `rate_limiting.max_requests_per_minute` | int | `60` | Steady-state rate limit per principal |
| `rate_limiting.burst_size` | int | `10` | Maximum burst above the rate limit |

## What's Next

Congratulations -- you've completed the sequential path. Your setup has health checks, circuit breakers, failover, load balancing, and rate limiting.

The remaining recipes are standalone. Start with [07 -- Observability: Metrics](07-observability-metrics.md) to add Prometheus and Grafana monitoring.
