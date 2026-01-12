# Contributing

See [docs/development/CONTRIBUTING.md](docs/development/CONTRIBUTING.md) for the full contributing guide.

## Quick Start

```bash
git clone https://github.com/mapyr/mcp-hangar.git
cd mcp-hangar
uv sync --extra dev
uv run pre-commit install
uv run pytest tests/ -v -m "not slow"
```

## Code of Conduct

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before contributing.
