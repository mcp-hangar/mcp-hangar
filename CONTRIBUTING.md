# Contributing

See [docs/development/CONTRIBUTING.md](docs/development/CONTRIBUTING.md) for the full contributing guide.

## Monorepo Structure

MCP Hangar is a monorepo containing multiple packages:

| Package | Language | Location |
|---------|----------|----------|
| Core | Python | `packages/core/` |
| Kubernetes Operator | Go | `packages/operator/` |
| Helm Charts | YAML | `packages/helm-charts/` |

## Quick Start

```bash
git clone https://github.com/mapyr/mcp-hangar.git
cd mcp-hangar

# Python core development
cd packages/core
pip install -e ".[dev]"
pytest

# Or use root Makefile
cd ../..
make setup
make test
```

## Code of Conduct

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before contributing.
