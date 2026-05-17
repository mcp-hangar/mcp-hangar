# Contributing

See [docs/development/CONTRIBUTING.md](https://github.com/mcp-hangar/docs/blob/main/development/CONTRIBUTING.md) for the full contributing guide.

## Monorepo Structure

MCP Hangar is a monorepo containing multiple packages:

| Package | Language | Location |
|---------|----------|----------|
| Core | Python | `packages/core/` |

## Quick Start

See [Git Flow](https://github.com/mcp-hangar/docs/blob/main/development/GIT_FLOW.md) for branching conventions and commit scopes.

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

MCP Hangar is licensed under the [MIT License](LICENSE).

## Code of Conduct

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before contributing.
