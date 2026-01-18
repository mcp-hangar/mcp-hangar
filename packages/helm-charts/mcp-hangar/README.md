# MCP-Hangar Helm Chart

Helm chart for deploying MCP-Hangar server on Kubernetes.

## Installation

```bash
helm install mcp-hangar oci://ghcr.io/mapyr/charts/mcp-hangar
```

## Configuration

See `values.yaml` for all available options.

### Basic Example

```bash
helm install mcp-hangar oci://ghcr.io/mapyr/charts/mcp-hangar \
  --set replicaCount=2 \
  --set resources.requests.memory=512Mi
```

### With Providers

```yaml
# values.yaml
providers:
  math:
    mode: subprocess
    command: ["python", "-m", "math_server"]
  fetch:
    mode: container
    image: ghcr.io/mapyr/mcp-fetch:latest
```

```bash
helm install mcp-hangar oci://ghcr.io/mapyr/charts/mcp-hangar -f values.yaml
```

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| replicaCount | int | `1` | Number of replicas |
| image.repository | string | `ghcr.io/mapyr/mcp-hangar` | Image repository |
| image.tag | string | `""` | Image tag (defaults to appVersion) |
| service.type | string | `ClusterIP` | Service type |
| service.port | int | `8080` | Service port |
| config.logLevel | string | `INFO` | Log level |
| config.jsonLogs | bool | `true` | Enable JSON logging |
| providers | object | `{}` | Provider configurations |
| serviceMonitor.enabled | bool | `false` | Enable Prometheus ServiceMonitor |

## License

MIT
