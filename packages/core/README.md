# MCP-Hangar Core

Production-grade infrastructure for Model Context Protocol.

> **Note**: This is the Python core package of the [MCP Hangar monorepo](https://github.com/mapyr/mcp-hangar). For Kubernetes operator, see `packages/operator/`. For Helm charts, see `packages/helm-charts/`.

## Installation

```bash
# Quick install (recommended)
curl -sSL https://get.mcp-hangar.io | bash

# Or via pip
pip install mcp-hangar
```

## Quick Start

```bash
# Run with config file
mcp-hangar serve --config config.yaml

# Or with environment variables
MCP_MODE=http MCP_HTTP_PORT=8080 mcp-hangar serve
```

## Development

```bash
# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check mcp_hangar
ruff format mcp_hangar

# Type check
mypy mcp_hangar
```

## Features

- **Provider Management**: Hot-load MCP providers (subprocess, Docker, remote)
- **CQRS + Event Sourcing**: Clean architecture with domain events
- **Health Monitoring**: Circuit breakers, automatic recovery
- **Observability**: Prometheus metrics, structured logging, tracing

## Documentation

See [main documentation](https://mapyr.github.io/mcp-hangar/) for details.

## License

MIT
