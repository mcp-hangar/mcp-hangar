# Contributing

See [docs/development/CONTRIBUTING.md](https://github.com/mcp-hangar/docs/blob/main/development/CONTRIBUTING.md) for the full contributing guide.

## Repository Structure

The Python core lives at the repository root. Related components — the
Kubernetes operator, agent, Helm charts, and Terraform provider — live in
separate repositories under the [mcp-hangar org](https://github.com/mcp-hangar).

| Package | Language | Location |
|---------|----------|----------|
| Core | Python | `src/mcp_hangar/` (repo root) |

## Quick Start

See [Git Flow](https://github.com/mcp-hangar/docs/blob/main/development/GIT_FLOW.md) for branching conventions and commit scopes.

```bash
git clone https://github.com/mcp-hangar/mcp-hangar.git
cd mcp-hangar

# Python core development (from the repo root)
pip install -e ".[dev]"
pytest

# Or use the root Makefile
make setup
make test
```

## Licensing

MCP Hangar is licensed under the [MIT License](LICENSE).

## Code of Conduct

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before contributing.
