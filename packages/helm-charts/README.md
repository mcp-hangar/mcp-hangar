# MCP-Hangar Helm Charts

Helm charts for deploying MCP-Hangar ecosystem on Kubernetes.

## Charts

- **[mcp-hangar](./mcp-hangar/)** - Core MCP-Hangar server
- **[mcp-hangar-operator](./mcp-hangar-operator/)** - Kubernetes operator for MCP providers

## Usage

### Install from OCI Registry

```bash
# Install core server
helm install mcp-hangar oci://ghcr.io/mapyr/charts/mcp-hangar

# Install operator
helm install mcp-hangar-operator oci://ghcr.io/mapyr/charts/mcp-hangar-operator \
  --namespace mcp-system \
  --create-namespace
```

### Install from Source

```bash
# From this directory
helm install mcp-hangar ./mcp-hangar
helm install mcp-hangar-operator ./mcp-hangar-operator
```

## Development

### Lint Charts

```bash
helm lint mcp-hangar
helm lint mcp-hangar-operator
```

### Test Template Rendering

```bash
helm template mcp-hangar ./mcp-hangar
helm template mcp-hangar-operator ./mcp-hangar-operator
```

### Package Charts

```bash
helm package mcp-hangar
helm package mcp-hangar-operator
```

## License

MIT
