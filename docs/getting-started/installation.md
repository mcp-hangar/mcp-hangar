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

MCP Hangar is organized as a monorepo:

```
mcp-hangar/
├── src/mcp_hangar/     # Python package (PyPI: mcp-hangar)
├── enterprise/         # BSL 1.1 licensed features
├── packages/
│   ├── operator/       # Kubernetes operator (Go)
│   └── helm-charts/    # Helm charts
```

### Python Core Package

```bash
git clone https://github.com/mcp-hangar/mcp-hangar.git
cd mcp-hangar
pip install -e .
```

### Development Installation

```bash
git clone https://github.com/mcp-hangar/mcp-hangar.git
cd mcp-hangar

# Install with dev dependencies
pip install -e ".[dev]"

# Or use uv from root
make setup
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
