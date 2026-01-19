# Installation

## Requirements

- Python 3.11 or higher
- Docker or Podman (for container providers)

## Install from PyPI

```bash
pip install mcp-hangar
```

## Install from Source (Monorepo)

MCP Hangar is organized as a monorepo with multiple packages:

```
mcp-hangar/
├── packages/
│   ├── core/           # Python package (PyPI: mcp-hangar)
│   ├── operator/       # Kubernetes operator (Go)
│   └── helm-charts/    # Helm charts
```

### Python Core Package

```bash
git clone https://github.com/mapyr/mcp-hangar.git
cd mcp-hangar/packages/core
pip install -e .
```

### Development Installation

```bash
git clone https://github.com/mapyr/mcp-hangar.git
cd mcp-hangar

# Install Python core with dev dependencies
cd packages/core
pip install -e ".[dev]"

# Or use uv from root
cd ../..
make setup
```

### Kubernetes Operator

```bash
cd packages/operator
make build
```

## Docker

```bash
docker pull ghcr.io/mapyr/mcp-hangar:latest

# Run with config
docker run -v $(pwd)/config.yaml:/app/config.yaml:ro \
  ghcr.io/mapyr/mcp-hangar:latest
```

## Helm Charts

```bash
# Install mcp-hangar
helm install mcp-hangar oci://ghcr.io/mapyr/charts/mcp-hangar

# Install Kubernetes operator
helm install mcp-hangar-operator oci://ghcr.io/mapyr/charts/mcp-hangar-operator \
  --namespace mcp-system --create-namespace
```

## Verify Installation

```bash
mcp-hangar --version
```
