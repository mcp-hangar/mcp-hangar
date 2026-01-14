# Installation

## Requirements

- Python 3.11 or higher
- Docker or Podman (for container providers)

## Install from PyPI

```bash
pip install mcp-hangar
```

## Install from Source

```bash
git clone https://github.com/mapyr/mcp-hangar.git
cd mcp-hangar
pip install -e .
```

## Development Installation

```bash
git clone https://github.com/mapyr/mcp-hangar.git
cd mcp-hangar
uv sync --extra dev
uv run pre-commit install
```

## Docker

```bash
docker pull ghcr.io/mapyr/mcp-hangar:latest

# Run with config
docker run -v $(pwd)/config.yaml:/app/config.yaml:ro \
  ghcr.io/mapyr/mcp-hangar:latest
```

## Verify Installation

```bash
mcp-hangar --version
```
