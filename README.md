# MCP Hangar

[![CI - Core](https://github.com/mapyr/mcp-hangar/actions/workflows/ci-core.yml/badge.svg)](https://github.com/mapyr/mcp-hangar/actions/workflows/ci-core.yml)
[![CI - Operator](https://github.com/mapyr/mcp-hangar/actions/workflows/ci-operator.yml/badge.svg)](https://github.com/mapyr/mcp-hangar/actions/workflows/ci-operator.yml)
[![PyPI](https://img.shields.io/pypi/v/mcp-hangar)](https://pypi.org/project/mcp-hangar/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://mapyr.github.io/mcp-hangar/)

**Production-grade MCP infrastructure with auto-discovery, observability, and resilience patterns.**

## Overview

MCP Hangar is a lifecycle management platform for Model Context Protocol providers, built for platform teams running MCP at scale. It replaces ad-hoc provider management with a unified control plane featuring auto-discovery from Kubernetes, Docker, and filesystem sources; circuit breakers and saga-based recovery for resilience; and first-class observability through Langfuse, OpenTelemetry, and Prometheus. The architecture follows Domain-Driven Design with CQRS and Event Sourcing, providing full audit trails for compliance-heavy environments.

## Why MCP Hangar?

| Challenge | Without MCP Hangar | With MCP Hangar |
|-----------|-------------------|-----------------|
| **Provider lifecycle** | Manual start/stop, no health monitoring | State machine with circuit breaker, health checks, automatic GC |
| **Observability** | None or DIY | Built-in Langfuse, OpenTelemetry, Prometheus metrics |
| **Dynamic environments** | Restart required for new providers | Auto-discovery from K8s, Docker, filesystem, entrypoints |
| **Failure handling** | Cascading failures | Circuit breaker, saga-based recovery and failover |
| **Audit & compliance** | None | Event sourcing with full audit trail |
| **Cold start latency** | Wait for provider startup | Predefined tools visible immediately, lazy loading |
| **Multi-provider routing** | Manual coordination | Load balancing with weighted round-robin, priority, least connections |

## Key Features

<details>
<summary><strong>🔄 Lifecycle Management</strong></summary>

Provider lifecycle follows a strict state machine:

```
COLD → INITIALIZING → READY ⇄ DEGRADED → DEAD
```

- **Lazy loading** — Providers start on first invocation, not at boot
- **Predefined tools** — Tool schemas visible before provider starts (no cold start for discovery)
- **Automatic GC** — Idle providers shutdown after configurable TTL
- **Graceful shutdown** — Clean termination with timeout enforcement

</details>

<details>
<summary><strong>🔍 Auto-Discovery</strong></summary>

Automatically detect and register providers from multiple sources:

| Source | Configuration |
|--------|---------------|
| **Kubernetes** | Pod annotations (`mcp-hangar.io/*`) with namespace filtering |
| **Docker/Podman** | Container labels (`mcp.hangar.*`) |
| **Filesystem** | YAML configs with file watching |
| **Python entrypoints** | `mcp.providers` entry point group |

Discovery modes:
- `additive` — Only adds providers, never removes (safe for static environments)
- `authoritative` — Adds and removes (for dynamic environments like K8s)

Conflict resolution: Static config > Kubernetes > Docker > Filesystem > Entrypoints

</details>

<details>
<summary><strong>📊 Observability</strong></summary>

Full observability stack for production operations:

**Langfuse Integration**
- End-to-end LLM tracing from prompt to provider response
- Cost attribution per provider, tool, user, or session
- Quality scoring and automated evals

**OpenTelemetry**
- Distributed tracing with context propagation
- OTLP export to Jaeger, Zipkin, or any collector

**Prometheus Metrics**
- Tool invocation latency and error rates
- Provider state transitions and cold starts
- Circuit breaker state and trip counts
- Health check results

**Health Endpoints**
- `/health/live` — Liveness check
- `/health/ready` — Readiness check (K8s compatible)
- `/health/startup` — Startup check
- `/metrics` — Prometheus scrape endpoint

</details>

<details>
<summary><strong>🛡️ Resilience</strong></summary>

Production-grade failure handling:

**Circuit Breaker**
- Opens after configurable failure threshold
- Auto-reset after timeout period
- Prevents cascading failures to healthy providers

**Saga-Based Recovery**
- `ProviderRecoverySaga` — Automatic restart with exponential backoff
- `ProviderFailoverSaga` — Failover to backup providers with auto-failback
- `GroupRebalanceSaga` — Rebalance traffic when members change

**Health Monitoring**
- Configurable check intervals
- Consecutive failure thresholds
- Automatic state transitions (READY → DEGRADED)

</details>

<details>
<summary><strong>🔒 Security</strong></summary>

Enterprise security controls:

- **Rate limiting** — Per-provider request limits
- **Input validation** — Schema validation before provider invocation
- **Secrets management** — Environment variable injection, never in config files
- **Container isolation** — Read-only filesystems, resource limits, network policies
- **Discovery security** — Namespace filtering, max providers per source, quarantine on failure

</details>

<details>
<summary><strong>🏗️ Architecture</strong></summary>

Domain-Driven Design with clean layer separation:

```
domain/         Core business logic, state machines, events, value objects
application/    Use cases, commands, queries, sagas
infrastructure/ Adapters for containers, subprocess, persistence, event bus
server/         MCP protocol handlers and validation
bootstrap/      Runtime initialization and dependency injection
```

- **CQRS** — Separate command and query paths
- **Event Sourcing** — All state changes emit domain events
- **Port/Adapter** — Extensible infrastructure layer
- **Thread-safe** — Lock hierarchy for concurrent access

</details>

## Quick Start

**Install:**
```bash
# Quick install (recommended)
curl -sSL https://get.mcp-hangar.io | bash

# Or via pip
pip install mcp-hangar
```

**Configure (`config.yaml`):**
```yaml
providers:
  math:
    mode: subprocess
    command: [python, -m, my_math_server]
    idle_ttl_s: 300

  sqlite:
    mode: container
    image: ghcr.io/modelcontextprotocol/server-sqlite:latest
    volumes:
      - "/data/sqlite:/data:rw"
```

**Run:**
```bash
# Stdio mode (Claude Desktop, Cursor, etc.)
mcp-hangar --config config.yaml

# HTTP mode (LM Studio, web clients)
mcp-hangar --config config.yaml --http
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        MCP Hangar                               │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    FastMCP Server                         │  │
│  │              (Stdio or HTTP transport)                    │  │
│  └──────────────────────────┬───────────────────────────────┘  │
│                             │                                   │
│  ┌──────────────────────────▼───────────────────────────────┐  │
│  │                  Provider Manager                         │  │
│  │    ┌─────────┐  ┌─────────┐  ┌─────────┐                 │  │
│  │    │ State   │  │ Health  │  │ Circuit │                 │  │
│  │    │ Machine │  │ Tracker │  │ Breaker │                 │  │
│  │    └─────────┘  └─────────┘  └─────────┘                 │  │
│  └──────────────────────────┬───────────────────────────────┘  │
│                             │                                   │
│  ┌──────────────────────────▼───────────────────────────────┐  │
│  │                    Providers                              │  │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────┐             │  │
│  │  │ Subprocess│  │  Docker   │  │  Remote   │             │  │
│  │  └───────────┘  └───────────┘  └───────────┘             │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  Background: [GC Worker] [Health Worker] [Discovery Worker]    │
└─────────────────────────────────────────────────────────────────┘
```

## Registry Tools

| Tool | Description |
|------|-------------|
| `registry_list` | List all providers with state, health status, and available tools |
| `registry_start` | Explicitly start a provider |
| `registry_stop` | Stop a running provider |
| `registry_invoke` | Invoke a tool on a provider (auto-starts if needed) |
| `registry_invoke_ex` | Invoke with retry, correlation ID, and metadata |
| `registry_invoke_stream` | Invoke with real-time progress notifications |
| `registry_tools` | Get tool schemas for a provider |
| `registry_details` | Get detailed information about a provider or group |
| `registry_health` | Get health status and metrics |
| `registry_status` | Dashboard view of all providers |
| `registry_discover` | Trigger discovery cycle |
| `registry_sources` | List discovery sources with status |
| `registry_quarantine` | List quarantined providers |
| `registry_approve` | Approve a quarantined provider |
| `registry_warm` | Pre-start providers to avoid cold start latency |

## Configuration Reference

| Option | Description | Default |
|--------|-------------|---------|
| `mode` | Provider mode: `subprocess`, `container`, `docker`, `remote`, `group` | required |
| `command` | Command for subprocess providers | — |
| `image` | Container image for container providers | — |
| `idle_ttl_s` | Seconds before idle provider shutdown | `300` |
| `health_check_interval_s` | Health check frequency in seconds | `60` |
| `max_consecutive_failures` | Failures before transition to DEGRADED | `3` |
| `tools` | Predefined tool schemas (visible before start) | — |
| `volumes` | Container volume mounts | — |
| `network` | Container network mode | `none` |
| `read_only` | Container read-only filesystem | `true` |

## Observability Setup

```yaml
observability:
  langfuse:
    enabled: true
    public_key: ${LANGFUSE_PUBLIC_KEY}
    secret_key: ${LANGFUSE_SECRET_KEY}
    host: https://cloud.langfuse.com

  tracing:
    enabled: true
    otlp_endpoint: http://localhost:4317

  metrics:
    enabled: true
    endpoint: /metrics
```

**Environment Variables:**
| Variable | Description |
|----------|-------------|
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OpenTelemetry collector endpoint |
| `MCP_TRACING_ENABLED` | Enable/disable tracing (`true`/`false`) |

**Endpoints:**
- `/metrics` — Prometheus metrics
- `/health/live` — Liveness probe
- `/health/ready` — Readiness probe
- `/health/startup` — Startup probe

## Documentation

📖 **[Full Documentation](https://mcp-hangar.io)**

- [Installation](https://mcp-hangar.io/getting-started/installation/)
- [Quick Start](https://mcp-hangar.io/getting-started/quickstart/)
- [Container Guide](https://mcp-hangar.io/guides/CONTAINERS/)
- [Auto-Discovery](https://mcp-hangar.io/guides/DISCOVERY/)
- [Observability](https://mcp-hangar.io/guides/OBSERVABILITY/)
- [Architecture](https://mcp-hangar.io/architecture/OVERVIEW/)

## Contributing

See [Contributing Guide](https://mcp-hangar.io/development/CONTRIBUTING/) for development setup, testing requirements, and code style.

```bash
git clone https://github.com/mapyr/mcp-hangar.git
cd mcp-hangar

# Setup Python core
cd packages/core
pip install -e ".[dev]"
pytest

# Or use root Makefile
cd ../..
make setup
make test
```

## Project Structure

```
mcp-hangar/
├── packages/
│   ├── core/                # Python package (PyPI: mcp-hangar)
│   │   ├── mcp_hangar/
│   │   ├── tests/
│   │   └── pyproject.toml
│   ├── operator/            # Kubernetes operator (Go)
│   │   ├── api/
│   │   ├── cmd/
│   │   └── go.mod
│   └── helm-charts/         # Helm charts
│       ├── mcp-hangar/
│       └── mcp-hangar-operator/
├── docs/                    # MkDocs documentation
├── examples/                # Quick starts & demos
├── monitoring/              # Grafana, Prometheus configs
└── Makefile                 # Root orchestration
```

## License

MIT License — see [LICENSE](LICENSE) for details.
