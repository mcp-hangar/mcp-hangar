# Provider Groups

Aggregate multiple providers behind a single virtual provider with load balancing, health tracking, and circuit breaker protection.

## Overview

Provider Groups allow you to treat multiple MCP providers as a single logical unit. MCP clients interact with the group as if it were one provider -- the group handles member selection, health monitoring, and failover automatically.

**Use groups when you need:**

- **High availability** -- If one provider fails, requests route to healthy members
- **Load distribution** -- Spread requests across multiple providers using configurable strategies
- **Failover** -- Designate primary and backup providers with priority-based routing
- **Capacity scaling** -- Add members to increase throughput without changing client configuration

### Group States

| State | Condition | Accepts Requests |
|-------|-----------|------------------|
| inactive | 0 healthy members | No |
| partial | healthy members < `min_healthy` | Yes (if circuit closed and healthy >= 1) |
| healthy | healthy members >= `min_healthy` | Yes |
| degraded | Circuit breaker open | No |

## Configuration

Groups are defined in `config.yaml` alongside regular providers. Set `mode: group` to create a group.

```yaml
providers:
  llm-pool:
    mode: group
    strategy: round_robin
    min_healthy: 1
    auto_start: true
    description: "LLM provider pool with failover"
    members:
      - id: llm-1
        mode: subprocess
        command: [python, -m, llm_server]
      - id: llm-2
        mode: subprocess
        command: [python, -m, llm_server]
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mode` | `str` | -- | Must be `"group"` |
| `strategy` | `str` | `"round_robin"` | Load balancing strategy |
| `min_healthy` | `int` | `1` | Minimum healthy members for `healthy` state |
| `auto_start` | `bool` | `true` | Auto-start members when the group is added |
| `description` | `str` | -- | Human-readable description |
| `members` | `list[dict]` | `[]` | Member provider configurations |

Each member entry accepts the same keys as a regular provider (`mode`, `command`, `image`, `endpoint`, `env`, `idle_ttl_s`, etc.) plus group-specific keys:

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `id` | `str` | required | -- | Unique member identifier |
| `weight` | `int` | `50` | 1-100 | Weight for weighted strategies |
| `priority` | `int` | `50` | 1-100 | Priority for priority strategy (lower = higher priority) |

For the full YAML schema, see the [Configuration Reference](../reference/configuration.md).

## Load Balancing Strategies

### Round Robin

Distributes requests sequentially across all healthy members. Each member receives an equal share of traffic.

```yaml
providers:
  api-pool:
    mode: group
    strategy: round_robin
    min_healthy: 1
    members:
      - id: api-1
        mode: subprocess
        command: [python, -m, api_server]
      - id: api-2
        mode: subprocess
        command: [python, -m, api_server]
      - id: api-3
        mode: subprocess
        command: [python, -m, api_server]
```

Requests cycle through members in order: api-1, api-2, api-3, api-1, api-2, ... Unhealthy members are skipped. No weight or priority configuration applies.

**Choose round robin when** all members have similar capacity and you want even distribution.

### Weighted Round Robin

Distributes requests proportionally based on member weights using the Nginx smooth weighted round-robin algorithm. Higher weight means more requests.

```yaml
providers:
  compute-pool:
    mode: group
    strategy: weighted_round_robin
    min_healthy: 1
    members:
      - id: large-instance
        mode: remote
        endpoint: https://large.example.com/mcp
        weight: 80
      - id: small-instance
        mode: remote
        endpoint: https://small.example.com/mcp
        weight: 20
```

With weights 80 and 20, `large-instance` receives approximately 4 out of every 5 requests. The smooth weighted algorithm avoids bursts -- requests interleave rather than sending 4 consecutive requests to one member.

**Choose weighted round robin when** members have different capacities (e.g., different hardware, instance sizes).

### Least Connections

Selects the member with the oldest `last_selected_at` timestamp, effectively routing to the least recently used member. This approximates least-connections behavior by distributing requests to the member that has been idle the longest.

```yaml
providers:
  db-pool:
    mode: group
    strategy: least_connections
    min_healthy: 2
    members:
      - id: db-reader-1
        mode: remote
        endpoint: https://db1.example.com/mcp
      - id: db-reader-2
        mode: remote
        endpoint: https://db2.example.com/mcp
      - id: db-reader-3
        mode: remote
        endpoint: https://db3.example.com/mcp
```

No weight or priority configuration applies. When multiple members have the same timestamp, the first healthy member is selected.

**Choose least connections when** requests have variable duration and you want to avoid overloading a member that is still processing a long request.

### Random

Selects a random healthy member using weighted probability. Members with higher weight have a proportionally higher chance of being selected.

```yaml
providers:
  search-pool:
    mode: group
    strategy: random
    min_healthy: 1
    members:
      - id: search-primary
        mode: subprocess
        command: [python, -m, search_server]
        weight: 70
      - id: search-secondary
        mode: subprocess
        command: [python, -m, search_server]
        weight: 30
```

With weights 70 and 30, `search-primary` has a 70% probability of being selected per request. Unlike round robin, there is no guaranteed ordering -- consecutive requests may go to the same member.

**Choose random when** you want simple probabilistic distribution without the overhead of tracking request order.

### Priority

Selects the healthy member with the lowest priority number. This creates a primary/backup pattern where backup members only receive traffic when higher-priority members are unavailable.

```yaml
providers:
  llm-failover:
    mode: group
    strategy: priority
    min_healthy: 1
    members:
      - id: local-llm
        mode: subprocess
        command: [python, -m, local_llm]
        priority: 1
      - id: cloud-llm
        mode: remote
        endpoint: https://llm-api.example.com/mcp
        priority: 50
      - id: fallback-llm
        mode: remote
        endpoint: https://fallback.example.com/mcp
        priority: 99
```

All requests go to `local-llm` (priority 1) while it is healthy. If `local-llm` becomes unhealthy, requests route to `cloud-llm` (priority 50). If both are down, `fallback-llm` (priority 99) handles traffic. When `local-llm` recovers and passes health checks, it resumes as the primary.

**Choose priority when** you have a preferred provider and want others to serve only as backups.

## Health Policy

The group tracks each member's health independently based on consecutive successes and failures.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `health.unhealthy_threshold` | `2` | Consecutive failures before a member is removed from rotation |
| `health.healthy_threshold` | `1` | Consecutive successes before a member is re-added to rotation |

```yaml
providers:
  resilient-pool:
    mode: group
    strategy: round_robin
    min_healthy: 2
    health:
      unhealthy_threshold: 3
      healthy_threshold: 2
    members:
      - id: worker-1
        mode: subprocess
        command: [python, -m, worker]
      - id: worker-2
        mode: subprocess
        command: [python, -m, worker]
      - id: worker-3
        mode: subprocess
        command: [python, -m, worker]
```

### Removal and Re-entry Flow

1. A member starts in rotation (healthy)
2. Each failed health check or invocation error increments `consecutive_failures`
3. When `consecutive_failures >= unhealthy_threshold`, the member is removed from rotation
4. While removed, the member continues to receive health checks
5. Each successful health check increments `consecutive_successes` and resets `consecutive_failures`
6. When `consecutive_successes >= healthy_threshold` AND the provider state is `READY`, the member re-enters rotation

!!! note
    A member must reach the `READY` provider state to re-enter rotation. Health check successes alone are not sufficient -- the underlying provider process must be fully initialized.

The `hangar_group_rebalance` tool can be used to manually trigger a health re-evaluation of all members, re-adding recovered members and removing failed ones.

## Circuit Breaker

The group-level circuit breaker protects against cascading failures by halting all requests when the total failure count exceeds a threshold.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `circuit_breaker.failure_threshold` | `10` | Total group failures before the circuit opens |
| `circuit_breaker.reset_timeout_s` | `60.0` | Seconds before the circuit auto-resets |

```yaml
providers:
  protected-pool:
    mode: group
    strategy: weighted_round_robin
    min_healthy: 1
    circuit_breaker:
      failure_threshold: 5
      reset_timeout_s: 30.0
    members:
      - id: svc-1
        mode: remote
        endpoint: https://svc1.example.com/mcp
        weight: 60
      - id: svc-2
        mode: remote
        endpoint: https://svc2.example.com/mcp
        weight: 40
```

### Circuit Breaker States

```
CLOSED (normal operation)
   |
   | total failures >= failure_threshold
   v
OPEN (all requests rejected)
   |
   | reset_timeout_s elapses
   v
CLOSED (normal operation resumes)
```

- **CLOSED** -- Normal operation. Requests are routed to healthy members. Each failure increments the failure counter.
- **OPEN** -- All requests are rejected immediately (the group enters the `degraded` state). No member selection occurs.
- **Auto-reset** -- After `reset_timeout_s` elapses, the next request attempt closes the circuit and resets the failure counter.

!!! warning
    The circuit breaker tracks total group failures, not per-member failures. A burst of errors from a single member can trip the breaker even if other members are healthy.

The `hangar_group_rebalance` tool resets the circuit breaker immediately, regardless of the timeout.

## Tool Access Filtering

Tool access filtering controls which tools are visible when invoking a group or its members. Filters use a three-level policy hierarchy with fnmatch glob pattern matching (`*`, `?`, `[seq]`).

### Policy Hierarchy

1. **Provider-level** -- Applied to the provider's own tool list
2. **Group-level** -- Applied to the group as a whole
3. **Member-level** -- Applied per member within the group

### Configuration

```yaml
providers:
  secure-pool:
    mode: group
    strategy: round_robin
    tools:
      allow_list: ["query_*", "search_*"]
      deny_list: []
    members:
      - id: full-access
        mode: subprocess
        command: [python, -m, data_server]
        tools:
          allow_list: []
          deny_list: ["admin_*"]
      - id: read-only
        mode: subprocess
        command: [python, -m, data_server]
        tools:
          allow_list: ["query_*"]
          deny_list: []
```

Individual providers can also define tool access policies:

```yaml
providers:
  restricted-provider:
    mode: subprocess
    command: [python, -m, server]
    tools:
      allow_list: ["safe_*"]
      deny_list: []
```

### Resolution Rules

| Condition | Behavior |
|-----------|----------|
| `allow_list` is set (non-empty) | Only tools matching an allow pattern are visible |
| `allow_list` is empty, `deny_list` is set | All tools visible except those matching a deny pattern |
| Both empty | All tools visible |
| Both set | `allow_list` takes precedence; `deny_list` is ignored |

Patterns use Python's `fnmatch` module:

- `*` matches everything
- `?` matches any single character
- `[seq]` matches any character in `seq`
- `[!seq]` matches any character not in `seq`
