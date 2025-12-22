# Provider Groups

Provider Groups allow you to combine multiple providers of the same type into a single logical unit with automatic load balancing, failover, and health management.

## Overview

A Provider Group:
- Presents as a single provider to clients
- Distributes requests across healthy members using configurable load balancing
- Automatically removes unhealthy members from rotation
- Includes circuit breaker protection for the entire group
- Supports automatic failover and recovery

## Configuration

### Basic Group Configuration

```yaml
providers:
  # A group of math providers with round-robin load balancing
  math:
    mode: group
    strategy: round_robin
    min_healthy: 1
    auto_start: true

    members:
      - id: math-node-1
        mode: subprocess
        command: [python, -m, examples.provider_math.server]

      - id: math-node-2
        mode: subprocess
        command: [python, -m, examples.provider_math.server]

      - id: math-node-3
        mode: subprocess
        command: [python, -m, examples.provider_math.server]
```

### Full Configuration Options

```yaml
providers:
  my-group:
    mode: group
    description: "High-availability math cluster"

    # Load balancing strategy
    # Options: round_robin, weighted_round_robin, least_connections, random, priority
    strategy: weighted_round_robin

    # Minimum healthy members required for the group to be considered healthy
    min_healthy: 2

    # Automatically start members when the group is created
    auto_start: true

    # Health thresholds
    health:
      # Failures before removing a member from rotation
      unhealthy_threshold: 2
      # Successes before adding a member back to rotation
      healthy_threshold: 1

    # Circuit breaker settings
    circuit_breaker:
      # Total failures across all members before circuit opens
      failure_threshold: 10
      # Time before attempting to close the circuit
      reset_timeout_s: 60.0

    members:
      - id: primary
        weight: 3          # Higher weight = more traffic (for weighted strategies)
        priority: 1        # Lower priority = preferred (for priority strategy)
        mode: subprocess
        command: [python, -m, my_provider]
        idle_ttl_s: 300

      - id: secondary
        weight: 1
        priority: 2
        mode: subprocess
        command: [python, -m, my_provider]
```

## Load Balancing Strategies

### Round Robin (`round_robin`)

Distributes requests evenly across all healthy members in order.

```yaml
strategy: round_robin
```

**Use case:** Equal-capacity members where you want predictable distribution.

### Weighted Round Robin (`weighted_round_robin`)

Distributes requests proportionally based on member weights. Higher weight = more requests.

```yaml
strategy: weighted_round_robin

members:
  - id: powerful-server
    weight: 3    # Gets 3x more requests
    mode: subprocess
    command: [...]

  - id: basic-server
    weight: 1
    mode: subprocess
    command: [...]
```

**Use case:** Members with different capacities or resource allocations.

### Least Connections (`least_connections`)

Selects the member that was least recently used, approximating even load distribution.

```yaml
strategy: least_connections
```

**Use case:** When request processing times vary significantly.

### Random (`random`)

Randomly selects a member, with optional weight consideration.

```yaml
strategy: random
```

**Use case:** Simple distribution when order doesn't matter.

### Priority (`priority`)

Always selects the member with the lowest priority number (highest priority). Falls back to higher priority members only when the primary is unavailable.

```yaml
strategy: priority

members:
  - id: primary
    priority: 1    # Always preferred when healthy
    mode: subprocess
    command: [...]

  - id: hot-standby
    priority: 2    # Used only when primary is down
    mode: subprocess
    command: [...]
```

**Use case:** Active/passive or primary/backup configurations.

## Group States

| State | Description |
|-------|-------------|
| `inactive` | No members are in rotation |
| `partial` | Some members healthy, but below `min_healthy` |
| `healthy` | At least `min_healthy` members in rotation |
| `degraded` | Circuit breaker is open |

## API Tools

### Standard Tools (Group-aware)

All standard registry tools work transparently with groups:

- `registry_list` - Lists both providers and groups
- `registry_start` - Starts all members in a group
- `registry_stop` - Stops all members in a group
- `registry_invoke` - Invokes on a selected member with automatic failover
- `registry_tools` - Gets tools from a healthy member
- `registry_details` - Returns group status with all member details

### Group-specific Tools

#### `registry_group_list`

Lists all groups with detailed status.

```json
{
  "groups": [
    {
      "group_id": "math",
      "state": "healthy",
      "strategy": "round_robin",
      "min_healthy": 1,
      "healthy_count": 2,
      "total_members": 3,
      "is_available": true,
      "circuit_open": false,
      "members": [
        {"id": "math-1", "state": "ready", "in_rotation": true, "weight": 1, "priority": 1},
        {"id": "math-2", "state": "ready", "in_rotation": true, "weight": 1, "priority": 1},
        {"id": "math-3", "state": "cold", "in_rotation": false, "weight": 1, "priority": 1}
      ]
    }
  ]
}
```

#### `registry_group_rebalance`

Manually triggers rebalancing for a group. Re-evaluates health of all members and updates rotation.

```python
result = registry_group_rebalance(group="math")
# {
#   "group_id": "math",
#   "state": "healthy",
#   "healthy_count": 2,
#   "total_members": 3,
#   "members_in_rotation": ["math-1", "math-2"]
# }
```

## Health and Circuit Breaker

### Member Health

Each member tracks:
- `consecutive_failures` - Incremented on failure, reset on success
- `consecutive_successes` - Incremented on success, used for recovery

When `consecutive_failures >= unhealthy_threshold`:
- Member is removed from rotation
- Event `GroupMemberHealthChanged` is emitted

When a removed member has `consecutive_successes >= healthy_threshold`:
- Member is added back to rotation
- Event `GroupMemberHealthChanged` is emitted

### Circuit Breaker

The group-level circuit breaker prevents cascading failures:

1. **Closed (Normal)**: Requests are routed to healthy members
2. **Open**: All requests fail immediately (after `failure_threshold` reached)
3. **Half-Open**: After `reset_timeout_s`, one request is allowed through
4. **Closed Again**: If the test request succeeds, circuit closes

## Automatic Retry

When invoking a tool on a group:

1. A member is selected using the load balancer
2. If the invocation fails:
   - The failure is reported to the group
   - If other healthy members exist, one retry is attempted on a different member
3. Success/failure metrics are updated

## Events

Groups emit the following domain events:

| Event | Description |
|-------|-------------|
| `GroupCreated` | Group was created |
| `GroupMemberAdded` | Member added to group |
| `GroupMemberRemoved` | Member removed from group |
| `GroupMemberHealthChanged` | Member's rotation status changed |
| `GroupStateChanged` | Group state transitioned |
| `GroupCircuitOpened` | Circuit breaker opened |
| `GroupCircuitClosed` | Circuit breaker closed |

## Example: High-Availability Setup

```yaml
providers:
  # High-availability LLM cluster
  llm-cluster:
    mode: group
    description: "Production LLM cluster with failover"
    strategy: weighted_round_robin
    min_healthy: 2

    health:
      unhealthy_threshold: 3
      healthy_threshold: 2

    circuit_breaker:
      failure_threshold: 15
      reset_timeout_s: 30.0

    members:
      # Primary GPU server
      - id: llm-gpu-1
        weight: 5
        priority: 1
        mode: container
        image: ghcr.io/myorg/llm-server:latest
        resources:
          memory: 16g
          cpu: "8.0"
        env:
          CUDA_VISIBLE_DEVICES: "0,1"

      # Secondary GPU server
      - id: llm-gpu-2
        weight: 5
        priority: 1
        mode: container
        image: ghcr.io/myorg/llm-server:latest
        resources:
          memory: 16g
          cpu: "8.0"

      # CPU fallback
      - id: llm-cpu-fallback
        weight: 1
        priority: 2
        mode: container
        image: ghcr.io/myorg/llm-server:cpu
        resources:
          memory: 8g
          cpu: "4.0"
```

## Monitoring

### Metrics

The following Prometheus metrics are available for groups:

```
# Number of members currently in rotation
mcp_group_members_in_rotation{group_id="math"} 2

# Group state (0=inactive, 1=partial, 2=healthy, 3=degraded)
mcp_group_state{group_id="math"} 2

# Total requests routed to each member
mcp_group_member_requests_total{group_id="math", member_id="math-1"} 150
```

### Health Endpoint

The `/health` endpoint includes group information:

```json
{
  "status": "healthy",
  "providers": {"total": 5, "by_state": {"ready": 3, "cold": 2}},
  "groups": {
    "total": 1,
    "by_state": {"healthy": 1},
    "total_members": 3,
    "healthy_members": 2
  }
}
```

## Best Practices

1. **Set `min_healthy` appropriately**: For critical services, ensure enough members to handle expected load even during failures.

2. **Use weighted strategies for heterogeneous clusters**: When members have different capacities, use `weighted_round_robin` to optimize throughput.

3. **Configure circuit breaker thresholds**: Set `failure_threshold` high enough to avoid false positives but low enough to protect against cascading failures.

4. **Monitor group health**: Set up alerts on `mcp_group_state` changes and circuit breaker events.

5. **Test failover scenarios**: Regularly test that your group configuration handles member failures gracefully.
