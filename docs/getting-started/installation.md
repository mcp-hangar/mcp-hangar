# Installation

## Requirements

- Python 3.11 or higher
- Docker or Podman (for container providers)

## Quick Install (Recommended)

```bash
curl -sSL https://mcp-hangar.io/install.sh | bash
```

This will install the latest version of MCP Hangar and set up your environment.

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
git clone https://github.com/mcp-hangar/mcp-hangar.git
cd mcp-hangar/packages/core
pip install -e .
```

### Development Installation

```bash
git clone https://github.com/mcp-hangar/mcp-hangar.git
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
docker pull ghcr.io/mcp-hangar/mcp-hangar:latest

# Run with config
docker run -v $(pwd)/config.yaml:/app/config.yaml:ro \
  ghcr.io/mcp-hangar/mcp-hangar:latest
```

## Helm Charts

```bash
# Install mcp-hangar
helm install mcp-hangar oci://ghcr.io/mcp-hangar/charts/mcp-hangar

# Install Kubernetes operator
helm install mcp-hangar-operator oci://ghcr.io/mcp-hangar/charts/mcp-hangar-operator \
  --namespace mcp-system --create-namespace
```

## Verify Installation

```bash
mcp-hangar --version
```
