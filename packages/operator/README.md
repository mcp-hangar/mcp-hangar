# MCP-Hangar Kubernetes Operator

Kubernetes Operator for managing MCP (Model Context Protocol) providers as native Kubernetes resources.

> **Note**: This is part of the [MCP Hangar monorepo](https://github.com/mapyr/mcp-hangar). For Python core package, see `packages/core/`. For Helm charts, see `packages/helm-charts/`.

## Features

- **MCPProvider CRD**: Declarative management of MCP tool providers
- **MCPProviderGroup CRD**: Load balancing and high availability for provider groups
- **MCPDiscoverySource CRD**: Automatic provider discovery from namespaces
- **State Machine**: Automatic lifecycle management (Cold → Initializing → Ready → Degraded → Dead)
- **Health Checks**: Configurable health monitoring with circuit breaker
- **Metrics**: Prometheus metrics for monitoring
- **Secure by Default**: Pod security contexts, non-root, read-only filesystem

## Quick Start

### Prerequisites

- Kubernetes 1.25+
- Helm 3.x
- kubectl configured for your cluster

### Installation

```bash
# Install via OCI registry
helm install mcp-hangar-operator oci://ghcr.io/mapyr/charts/mcp-hangar-operator \
  --namespace mcp-system \
  --create-namespace
```

### Create Your First Provider

```yaml
apiVersion: mcp-hangar.io/v1alpha1
kind: MCPProvider
metadata:
  name: my-tools
  namespace: default
spec:
  mode: container
  image: ghcr.io/my-org/my-mcp-tools:latest
  replicas: 1
  resources:
    requests:
      memory: "128Mi"
      cpu: "100m"
```

```bash
kubectl apply -f my-provider.yaml
kubectl get mcpproviders
```

## CRD Reference

### MCPProvider

| Field | Description | Default |
|-------|-------------|---------|
| `spec.mode` | Execution mode: `container` or `remote` | Required |
| `spec.image` | Container image (for container mode) | Required for container |
| `spec.endpoint` | HTTP endpoint (for remote mode) | Required for remote |
| `spec.replicas` | Number of replicas (0 = cold start) | `1` |
| `spec.idleTTL` | Idle timeout before shutdown | `5m` |
| `spec.healthCheck.enabled` | Enable health checks | `true` |
| `spec.healthCheck.interval` | Health check interval | `30s` |
| `spec.circuitBreaker.enabled` | Enable circuit breaker | `true` |

### MCPProviderGroup

| Field | Description | Default |
|-------|-------------|---------|
| `spec.selector` | Label selector for providers | Required |
| `spec.strategy` | Load balancing: RoundRobin, LeastConnections, Random, Failover | `RoundRobin` |
| `spec.failover.maxRetries` | Maximum retry attempts | `2` |
| `spec.healthPolicy.minHealthyPercentage` | Minimum healthy providers | `50` |

### MCPDiscoverySource

| Field | Description | Default |
|-------|-------------|---------|
| `spec.type` | Discovery type: Namespace, ConfigMap, Annotations | Required |
| `spec.mode` | Discovery mode: Additive, Authoritative | `Additive` |
| `spec.refreshInterval` | Rescan interval | `1m` |

## Examples

See the `examples/kubernetes/` directory for complete examples:

- `basic-provider.yaml` - Simple container provider
- `provider-with-secrets.yaml` - Provider using Kubernetes Secrets
- `remote-provider.yaml` - External endpoint provider
- `provider-group-ha.yaml` - High availability group
- `discovery-source.yaml` - Automatic discovery

## Metrics

The operator exposes Prometheus metrics at `:8080/metrics`:

| Metric | Description |
|--------|-------------|
| `mcp_operator_reconcile_total` | Total reconciliations |
| `mcp_operator_reconcile_duration_seconds` | Reconciliation duration |
| `mcp_operator_provider_state` | Provider state (1 = in state) |
| `mcp_operator_provider_tools_count` | Tools per provider |
| `mcp_operator_provider_health_check_failures_total` | Health check failures |

## Development

```bash
# Run locally
make run

# Run all tests
make test

# Run tests with coverage
go test ./... -cover

# Run specific package tests
go test ./pkg/provider -v
go test ./pkg/hangar -v
go test ./pkg/metrics -v
go test ./internal/controller -v

# Build image
make docker-build IMG=my-registry/mcp-hangar-operator:v0.1.0

# Push image
make docker-push IMG=my-registry/mcp-hangar-operator:v0.1.0
```

### Testing

**Test Coverage: 37 tests ✅ 100% passing**

- **pkg/provider**: 14 tests (Pod builder)
- **pkg/hangar**: 11 tests (Hangar client)
- **pkg/metrics**: 10 tests (Prometheus metrics)
- **internal/controller**: 2 tests (Controller config)

See [TESTING.md](TESTING.md) for detailed testing documentation.

## License

MIT License
