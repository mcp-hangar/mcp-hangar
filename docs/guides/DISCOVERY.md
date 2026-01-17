# Provider Discovery

Auto-discover MCP providers from Docker, Kubernetes, filesystem, or Python entrypoints.

## Configuration

```yaml
discovery:
  enabled: true
  refresh_interval_s: 30
  auto_register: true

  sources:
    - type: docker
      mode: additive
```

## Sources

### Docker/Podman

```yaml
sources:
  - type: docker
    mode: additive
    socket_path: /var/run/docker.sock  # optional
```

Container labels:

```yaml
# docker-compose.yml
services:
  my-provider:
    image: my-mcp-server:latest
    labels:
      mcp.hangar.enabled: "true"
      mcp.hangar.name: "my-provider"
      mcp.hangar.mode: "http"
      mcp.hangar.port: "8080"
```

| Label | Required | Default |
|-------|----------|---------|
| `mcp.hangar.enabled` | yes | - |
| `mcp.hangar.name` | no | container name |
| `mcp.hangar.mode` | no | `http` |
| `mcp.hangar.port` | no | `8080` |
| `mcp.hangar.group` | no | - |

### Kubernetes

```yaml
sources:
  - type: kubernetes
    mode: authoritative
    namespaces: [mcp-providers]
    label_selector: "app.kubernetes.io/component=mcp-provider"
    in_cluster: true
```

Pod annotations:

```yaml
apiVersion: v1
kind: Pod
metadata:
  annotations:
    mcp-hangar.io/enabled: "true"
    mcp-hangar.io/name: "data-processor"
    mcp-hangar.io/mode: "http"
    mcp-hangar.io/port: "8080"
```

### Filesystem

```yaml
sources:
  - type: filesystem
    mode: additive
    path: /etc/mcp-hangar/providers.d/
    pattern: "*.yaml"
    watch: true
```

Provider file:

```yaml
# /etc/mcp-hangar/providers.d/custom.yaml
name: custom-tool
enabled: true
mode: subprocess
connection:
  command: python
  args: [-m, my_mcp_server]
```

### Python Entrypoints

```yaml
sources:
  - type: entrypoint
    mode: additive
    group: mcp.providers
```

```toml
# pyproject.toml
[project.entry-points."mcp.providers"]
my_provider = "my_package.server:create_server"
```

```python
def create_server():
    return {
        "name": "my-tools",
        "mode": "subprocess",
        "command": ["python", "-m", "my_package.server"]
    }
```

## Discovery Modes

| Mode | Behavior |
|------|----------|
| `additive` | Only adds providers, never removes |
| `authoritative` | Adds and removes (for dynamic environments) |

## Security

```yaml
discovery:
  security:
    max_providers_per_source: 100
    max_registration_rate: 10  # per minute
    require_health_check: true
    quarantine_on_failure: true
    allowed_namespaces: [mcp-providers]
    denied_namespaces: [kube-system, default]
```

## Tools

| Tool | Description |
|------|-------------|
| `registry_discover` | Trigger discovery cycle |
| `registry_sources` | List sources with status |
| `registry_quarantine` | List quarantined providers |
| `registry_approve` | Approve quarantined provider |

## Conflict Resolution

1. **Static config wins** — Manual config always takes precedence
2. **Higher priority source wins** — K8s (1) > Docker (2) > Filesystem (3) > Entrypoints (4)
3. **TTL expiration** — Authoritative sources deregister after TTL

## Prometheus Metrics

| Metric | Description |
|--------|-------------|
| `mcp_hangar_discovery_providers_total` | Providers per source |
| `mcp_hangar_discovery_registrations_total` | New registrations |
| `mcp_hangar_discovery_errors_total` | Errors by source |
| `mcp_hangar_discovery_latency_seconds` | Cycle duration |
