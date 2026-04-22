# Kubernetes Integration

Deploy and manage MCP servers as native Kubernetes resources using the MCP-Hangar Operator.

## Overview

The MCP-Hangar Operator provides:

- **MCPServer** - Declarative MCP server management
- **MCPServerGroup** - Load balancing and high availability
- **MCPDiscoverySource** - Automatic MCP server discovery

## Installation

### Prerequisites

- Kubernetes 1.25+
- Helm 3.x
- kubectl configured for your cluster

### Install CRDs

```bash
# Install Custom Resource Definitions
kubectl apply -f https://raw.githubusercontent.com/mcp-hangar/mcp-hangar/main/deploy/crds/mcpserver.yaml
kubectl apply -f https://raw.githubusercontent.com/mcp-hangar/mcp-hangar/main/deploy/crds/mcpservergroup.yaml
kubectl apply -f https://raw.githubusercontent.com/mcp-hangar/mcp-hangar/main/deploy/crds/mcpdiscoverysource.yaml

# Verify
kubectl get crds | grep mcp-hangar.io
```

### Install Operator via Helm

```bash
# Add Helm repository
helm repo add mcp-hangar https://mcp-hangar.io/charts
helm repo update

# Install operator
helm install mcp-hangar-operator mcp-hangar/mcp-hangar-operator \
  --namespace mcp-system \
  --create-namespace \
  --set hangar.url=http://mcp-hangar-core:8080

# Verify
kubectl get pods -n mcp-system
```

### Configuration

```yaml
# values.yaml
operator:
  logLevel: info
  metrics:
    enabled: true
    port: 8080
  leaderElection:
    enabled: true

hangar:
  url: "http://mcp-hangar-core.mcp-system.svc.cluster.local:8080"
  existingSecret: "mcp-hangar-credentials"
  secretKey: "api-key"

resources:
  limits:
    cpu: 500m
    memory: 256Mi
  requests:
    cpu: 100m
    memory: 128Mi
```

## MCPServer

### Basic MCP Server

```yaml
apiVersion: mcp-hangar.io/v1alpha1
kind: MCPServer
metadata:
  name: sqlite-tools
  namespace: mcp-servers
spec:
  mode: container
  image: ghcr.io/modelcontextprotocol/mcp-sqlite:latest
  replicas: 1

  idleTTL: "10m"
  startupTimeout: "60s"

  resources:
    requests:
      memory: "128Mi"
      cpu: "100m"
    limits:
      memory: "512Mi"
      cpu: "500m"

  env:
    - name: SQLITE_DB_PATH
      value: /data/database.db

  healthCheck:
    enabled: true
    interval: "30s"
    failureThreshold: 3
```

### MCP Server with Secrets

```yaml
apiVersion: mcp-hangar.io/v1alpha1
kind: MCPServer
metadata:
  name: github-tools
  namespace: mcp-servers
spec:
  mode: container
  image: ghcr.io/modelcontextprotocol/mcp-github:latest

  env:
    - name: GITHUB_TOKEN
      valueFrom:
        secretKeyRef:
          name: github-credentials
          key: token

  tools:
    allowList:
      - create_issue
      - list_issues
    rateLimit:
      requestsPerMinute: 30
```

### Remote MCP Server

```yaml
apiVersion: mcp-hangar.io/v1alpha1
kind: MCPServer
metadata:
  name: external-api
  namespace: mcp-servers
spec:
  mode: remote
  endpoint: https://api.example.com/mcp

  healthCheck:
    enabled: true
    interval: "1m"
    timeout: "10s"

  circuitBreaker:
    enabled: true
    failureThreshold: 5
    resetTimeout: "30s"
```

### Cold Start (Scale to Zero)

```yaml
apiVersion: mcp-hangar.io/v1alpha1
kind: MCPServer
metadata:
  name: expensive-tool
spec:
  mode: container
  image: ghcr.io/my-org/expensive-tool:latest

  # Start with 0 replicas - will start on first request
  replicas: 0

  # Shutdown after 5 minutes of inactivity
  idleTTL: "5m"
```

## MCPServerGroup

### High Availability Group

```yaml
apiVersion: mcp-hangar.io/v1alpha1
kind: MCPServerGroup
metadata:
  name: database-tools-ha
  namespace: mcp-servers
spec:
  # Select mcp_servers by label
  selector:
    matchLabels:
      mcp-hangar.io/category: database

  # Load balancing strategy
  strategy: LeastConnections  # RoundRobin, LeastConnections, Random, Failover

  # Failover configuration
  failover:
    enabled: true
    maxRetries: 2
    retryDelay: "500ms"

  # Health requirements
  healthPolicy:
    minHealthyPercentage: 50
    unhealthyThreshold: 3
```

### Label MCP servers for Grouping

```yaml
apiVersion: mcp-hangar.io/v1alpha1
kind: MCPServer
metadata:
  name: sqlite-primary
  labels:
    mcp-hangar.io/category: database
    mcp-hangar.io/tier: primary
spec:
  mode: container
  image: ghcr.io/modelcontextprotocol/mcp-sqlite:latest
---
apiVersion: mcp-hangar.io/v1alpha1
kind: MCPServer
metadata:
  name: sqlite-replica
  labels:
    mcp-hangar.io/category: database
    mcp-hangar.io/tier: replica
spec:
  mode: container
  image: ghcr.io/modelcontextprotocol/mcp-sqlite:latest
```

## MCPDiscoverySource

### Namespace Discovery

```yaml
apiVersion: mcp-hangar.io/v1alpha1
kind: MCPDiscoverySource
metadata:
  name: team-mcp-servers
  namespace: mcp-system
spec:
  type: Namespace
  mode: Authoritative  # Additive or Authoritative
  refreshInterval: "5m"

  namespaceSelector:
    matchLabels:
      mcp-hangar.io/enabled: "true"

  providerTemplate:
    spec:
      idleTTL: "5m"
      resources:
        requests:
          memory: "64Mi"
          cpu: "50m"
```

### ConfigMap Discovery

```yaml
apiVersion: mcp-hangar.io/v1alpha1
kind: MCPDiscoverySource
metadata:
  name: config-mcp-servers
spec:
  type: ConfigMap
  refreshInterval: "1m"

  configMapRef:
    name: mcp-server-definitions
    namespace: mcp-config
```

## Security

### Pod Security

All MCP server pods run with secure defaults:

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 65534
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities:
    drop:
      - ALL
```

Override if needed:

```yaml
apiVersion: mcp-hangar.io/v1alpha1
kind: MCPServer
metadata:
  name: my-mcp-server
spec:
  securityContext:
    runAsUser: 1000
    readOnlyRootFilesystem: false  # If mcp_server needs writable fs
```

### RBAC

The operator requires cluster-level permissions:

```yaml
# Automatically created by Helm chart
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: mcp-hangar-operator
rules:
  - apiGroups: [mcp-hangar.io]
    resources: [mcpservers, mcpservergroups, mcpdiscoverysources]
    verbs: [get, list, watch, create, update, patch, delete]
  - apiGroups: [""]
    resources: [pods, secrets, configmaps]
    verbs: [get, list, watch, create, update, patch, delete]
```

### Network Policies

Restrict MCP server communication:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: mcp-server-isolation
  namespace: mcp-servers
spec:
  podSelector:
    matchLabels:
      mcp-hangar.io/mcp_server: "true"
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              mcp-hangar.io/core: "true"
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              mcp-hangar.io/core: "true"
```

## Monitoring

### Prometheus Metrics

The operator exposes metrics at `:8080/metrics`:

| Metric | Type | Description |
|--------|------|-------------|
| `mcp_operator_reconcile_total` | Counter | Total reconciliations |
| `mcp_operator_reconcile_duration_seconds` | Histogram | Reconciliation duration |
| `mcp_operator_provider_state` | Gauge | MCP server state (1 = active) |
| `mcp_operator_provider_tools_count` | Gauge | Tools per MCP server |
| `mcp_operator_provider_health_check_failures_total` | Counter | Health check failures |

### ServiceMonitor

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: mcp-hangar-operator
  namespace: mcp-system
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: mcp-hangar-operator
  endpoints:
    - port: metrics
      interval: 30s
```

### Alerts

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: mcp-hangar-alerts
spec:
  groups:
    - name: mcp-hangar
      rules:
        - alert: MCPServerDegraded
          expr: mcp_operator_provider_state{state="Degraded"} == 1
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "MCP MCP Server {{ $labels.name }} is degraded"

        - alert: MCPServerDead
          expr: mcp_operator_provider_state{state="Dead"} == 1
          for: 2m
          labels:
            severity: critical
          annotations:
            summary: "MCP MCP Server {{ $labels.name }} is dead"
```

## Troubleshooting

### Check MCP Server Status

```bash
# List all mcp_servers
kubectl get mcpservers -A

# Describe specific mcp_server
kubectl describe mcpserver my-mcp-server -n mcp-servers

# Check conditions
kubectl get mcpserver my-mcp-server -o jsonpath='{.status.conditions}'
```

### Check Operator Logs

```bash
kubectl logs -n mcp-system deployment/mcp-hangar-operator -f
```

### Common Issues

**MCP Server stuck in Initializing:**

- Check pod logs: `kubectl logs mcp-MCP server-<name> -n <namespace>`
- Verify image exists and is pullable
- Check resource limits

**MCP Server in Degraded state:**

- Health checks failing
- Check network connectivity to MCP server
- Verify MCP-Hangar core is running

**Discovery not finding MCP servers:**

- Verify namespace labels match selector
- Check MCPDiscoverySource status
- Review operator logs for discovery errors

## API Reference

### MCPServer Spec

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `mode` | string | Yes | - | `container` or `remote` |
| `image` | string | For container | - | Container image |
| `endpoint` | string | For remote | - | HTTP endpoint URL |
| `replicas` | int | No | `1` | Desired replicas (0 = cold) |
| `idleTTL` | duration | No | `5m` | Idle timeout |
| `startupTimeout` | duration | No | `30s` | Startup timeout |
| `healthCheck` | object | No | enabled | Health check config |
| `resources` | object | No | - | Resource requirements |
| `env` | array | No | - | Environment variables |
| `volumes` | array | No | - | Volume mounts |
| `securityContext` | object | No | secure defaults | Security context |
| `serviceAccountName` | string | No | - | ServiceAccount |
| `nodeSelector` | map | No | - | Node selection |
| `tolerations` | array | No | - | Tolerations |
| `tools` | object | No | - | Tool configuration |
| `circuitBreaker` | object | No | enabled | Circuit breaker config |

### MCPServer Status

| Field | Type | Description |
|-------|------|-------------|
| `state` | string | Cold, Initializing, Ready, Degraded, Dead |
| `replicas` | int | Current replicas |
| `readyReplicas` | int | Ready replicas |
| `toolsCount` | int | Available tools |
| `tools` | array | Tool names |
| `lastStartedAt` | time | Last start time |
| `lastHealthCheck` | time | Last health check |
| `consecutiveFailures` | int | Failure count |
| `conditions` | array | Status conditions |

## Examples

See [examples/kubernetes/](https://mcp-hangar.io/examples/kubernetes/) for complete examples.
