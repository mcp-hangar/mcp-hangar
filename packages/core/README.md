# MCP-Hangar Core

Production-grade infrastructure for Model Context Protocol.

## Installation

```bash
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

See [main documentation](https://docs.mcp-hangar.io) for details.

## License

MIT
