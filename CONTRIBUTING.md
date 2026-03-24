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
git clone https://github.com/mcp-hangar/mcp-hangar.git
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

## Licensing

MCP Hangar uses a dual-license model:

- **Core** (`src/`, `packages/`) -- [MIT License](LICENSE). No CLA required.
- **Enterprise** (`enterprise/`) -- [BSL 1.1](enterprise/LICENSE.BSL). [CLA](CLA.md) required.

If your contribution touches any file under `enterprise/`, you must agree to the [Contributor License Agreement](CLA.md) by including the CLA statement in your pull request description.

## Code of Conduct

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before contributing.
