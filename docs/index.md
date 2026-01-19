# MCP Hangar

[![CI - Core](https://github.com/mapyr/mcp-hangar/actions/workflows/ci-core.yml/badge.svg)](https://github.com/mapyr/mcp-hangar/actions/workflows/ci-core.yml)
[![CI - Operator](https://github.com/mapyr/mcp-hangar/actions/workflows/ci-operator.yml/badge.svg)](https://github.com/mapyr/mcp-hangar/actions/workflows/ci-operator.yml)
[![PyPI](https://img.shields.io/pypi/v/mcp-hangar)](https://pypi.org/project/mcp-hangar/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://mapyr.github.io/mcp-hangar/)

Production-grade MCP provider registry with lazy loading, health monitoring, and container support.

## Monorepo Structure

MCP Hangar is a monorepo containing multiple packages:

| Package | Description | Location |
|---------|-------------|----------|
| **Core** | Python library (PyPI: `mcp-hangar`) | `packages/core/` |
| **Kubernetes Operator** | Go-based K8s operator | `packages/operator/` |
| **Helm Charts** | Deployment charts | `packages/helm-charts/` |

## Features

- **Lazy Loading** — Providers start only when invoked, tools visible immediately
- **Container Support** — Docker/Podman with auto-detection
- **Provider Groups** — Load balancing with multiple strategies
- **Health Monitoring** — Circuit breaker pattern with automatic recovery
- **Auto-Discovery** — Detect providers from Docker labels, K8s annotations, filesystem
- **Automatic Retry** — Built-in retry with exponential backoff for transient failures
- **Real-Time Progress** — See operation progress while waiting
- **Rich Errors** — Human-readable errors with recovery hints
- **Kubernetes Native** — CRDs for declarative provider management

## Quick Start

```bash
pip install mcp-hangar
mcp-hangar --config config.yaml
```

## Documentation

📖 **[Full Documentation](https://mapyr.github.io/mcp-hangar/)**

- [Installation](https://mapyr.github.io/mcp-hangar/getting-started/installation/)
- [Quick Start Guide](https://mapyr.github.io/mcp-hangar/getting-started/quickstart/)
- [Architecture Overview](https://mapyr.github.io/mcp-hangar/architecture/OVERVIEW/)
- [Container Guide](https://mapyr.github.io/mcp-hangar/guides/CONTAINERS/)

## Contributing

See [Contributing Guide](development/CONTRIBUTING.md) for development setup and guidelines.

## License

MIT - see [LICENSE](https://github.com/mapyr/mcp-hangar/blob/main/LICENSE) for details.
