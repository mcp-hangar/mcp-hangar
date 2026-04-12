# mcp-hangar -- Python Core Platform

> DDD/CQRS runtime security and governance layer for MCP servers. Published to PyPI as `mcp-hangar`.

## Quick Reference

| Property | Value |
|----------|-------|
| Language | Python 3.11+ |
| Package manager | uv |
| Architecture | DDD + CQRS + Event Sourcing |
| License | BSL 1.1 (core), Enterprise (auth module) |
| Entry point | `mcp-hangar serve` or `python -m mcp_hangar.server` |

## Commands

```bash
# Setup
uv sync

# Test
uv run pytest tests/ -x -q
uv run pytest tests/unit/              # unit only
uv run pytest tests/integration/       # integration (needs Docker)
uv run pytest -m benchmark             # benchmarks

# Lint & type check
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run mypy src/

# Run
mcp-hangar serve --http --port 8000
mcp-hangar serve                        # stdio mode (MCP protocol)
mcp-hangar init                         # setup wizard
mcp-hangar add <provider>               # install from registry

# Build
uv build
```

## Source Layout

```
mcp-hangar/
├── src/mcp_hangar/
│   ├── domain/                    # DDD core -- owns ALL contracts
│   │   ├── model/                 # Aggregates: Provider (1367 LOC), ProviderGroup, Tenant
│   │   │   ├── aggregate.py       # Base AggregateRoot with event collection
│   │   │   ├── provider.py        # Main aggregate: state machine, health, circuit breaker
│   │   │   ├── event_sourced_provider.py  # Rebuilds from event stream + snapshots
│   │   │   ├── provider_group.py  # Load balancing, failover
│   │   │   └── circuit_breaker.py # Circuit breaker state machine
│   │   ├── contracts/             # 17 interfaces (Dependency Inversion Principle)
│   │   │   ├── command.py         # CommandHandler ABC
│   │   │   ├── event_bus.py       # IEventBus
│   │   │   ├── event_store.py     # IEventStore
│   │   │   ├── persistence.py     # IAuditRepository, IProviderConfigRepository
│   │   │   ├── authentication.py  # IAuthenticator
│   │   │   └── authorization.py   # IAuthorizer
│   │   ├── value_objects/         # Immutable domain primitives
│   │   │   ├── provider.py        # ProviderState enum, ProviderMode, ProviderId
│   │   │   ├── config.py          # CommandLine, DockerImage, Endpoint
│   │   │   ├── security.py        # Principal, Role, Permission
│   │   │   └── capabilities.py    # ProviderCapabilities, ViolationType
│   │   ├── events.py              # 40+ domain events (1327 LOC)
│   │   ├── exceptions.py          # Domain-specific errors
│   │   ├── services/              # Domain services (stateless logic)
│   │   │   └── provider_launcher/ # Subprocess/Docker launcher (shell=False enforced)
│   │   ├── security/              # Input validation, rate limiting
│   │   ├── discovery/             # Conflict resolution (static always wins)
│   │   └── repository.py          # IProviderRepository + InMemoryProviderRepository
│   │
│   ├── application/               # CQRS handlers + sagas
│   │   ├── commands/              # 30+ command types with handlers
│   │   │   ├── commands.py        # StartProviderCommand, InvokeToolCommand, etc.
│   │   │   ├── handlers.py        # Command handlers (BaseProviderHandler pattern)
│   │   │   ├── crud_commands.py   # Create/Update/Delete commands
│   │   │   └── crud_handlers.py   # CRUD handlers with DIP
│   │   ├── queries/               # Read-side queries + handlers
│   │   │   ├── queries.py         # ListProvidersQuery, GetProviderQuery, etc.
│   │   │   └── handlers.py        # Return read models (flattened projections)
│   │   ├── event_handlers/        # 8 subscribers: logging, metrics, audit, security
│   │   ├── sagas/                 # Long-running transactions
│   │   │   ├── provider_failover_saga.py   # 3-step failover with compensation
│   │   │   ├── provider_recovery_saga.py   # Auto-restart degraded providers
│   │   │   └── group_rebalance_saga.py     # Rebalance on member failure
│   │   ├── ports/                 # ICommandBus, IQueryBus, ISagaManager
│   │   ├── read_models/           # Denormalized query projections
│   │   └── discovery/             # Discovery orchestration
│   │
│   ├── infrastructure/            # Adapters implementing domain contracts
│   │   ├── command_bus.py         # CommandBus with middleware pipeline
│   │   ├── query_bus.py           # QueryBus (1:1 handler mapping)
│   │   ├── event_bus.py           # In-process pub/sub
│   │   ├── event_store.py         # Event store abstraction
│   │   ├── event_sourced_repository.py  # Event sourcing with snapshots (every 50 events)
│   │   ├── saga_manager.py        # Saga orchestration + state persistence
│   │   ├── lock_hierarchy.py      # Deadlock prevention (lock ordering)
│   │   ├── single_flight.py       # Cold start deduplication
│   │   ├── persistence/           # SQLite/Postgres repositories, UoW, event upcasting
│   │   ├── discovery/             # 4 adapters: Kubernetes, Docker, filesystem, entrypoint
│   │   ├── identity/              # JWT/header identity extraction
│   │   ├── observability/         # OTLP audit export
│   │   └── truncation/            # Response truncation caching (memory/Redis)
│   │
│   ├── server/                    # HTTP/WebSocket/CLI/MCP protocol
│   │   ├── api/                   # FastAPI REST endpoints
│   │   │   ├── router.py          # Route registration
│   │   │   ├── providers.py       # /api/providers CRUD
│   │   │   ├── tools.py           # Tool invocation
│   │   │   ├── middleware.py      # Auth, error handling, CORS
│   │   │   └── ws/                # WebSocket real-time events
│   │   ├── cli/                   # Typer CLI (init, serve, add, status)
│   │   ├── bootstrap/             # Composition root (10 modules)
│   │   │   ├── cqrs.py            # Register all command/query handlers
│   │   │   ├── event_handlers.py  # Wire event subscribers
│   │   │   ├── enterprise.py      # Conditional auth bootstrap
│   │   │   └── workers.py         # Background task workers
│   │   └── tools/                 # MCP tool implementations
│   │       └── batch/             # Batch executor with concurrency control
│   │
│   ├── cloud/                     # Bridge to hangar-cloud gRPC/REST
│   ├── observability/             # Metrics, tracing conventions
│   ├── bootstrap/runtime.py       # Composition root (protocols + config)
│   └── facade.py                  # High-level API hiding complexity
│
├── enterprise/                    # Enterprise features (separate DDD boundary)
│   ├── auth/
│   │   ├── commands/              # CreateApiKeyCommand, AssignRoleCommand
│   │   ├── queries/               # Auth queries + handlers
│   │   ├── infrastructure/        # Authenticators, authorizers, stores
│   │   │   ├── api_key_authenticator.py   # Constant-time key comparison
│   │   │   ├── rbac_authorizer.py         # Role-based access control
│   │   │   ├── opa_authorizer.py          # Open Policy Agent
│   │   │   ├── sqlite_store.py            # SQLite auth storage
│   │   │   └── postgres_store.py          # PostgreSQL auth storage
│   │   ├── api/routes.py          # Auth REST endpoints
│   │   ├── cli.py                 # mcp-hangar auth <subcommand>
│   │   └── bootstrap.py           # Auth component bootstrap
│   ├── approvals/                 # Approval gate workflow
│   └── compliance/                # Compliance reporting
│
├── tests/
│   ├── conftest.py                # Shared fixtures, marker-based categorization
│   ├── unit/                      # Fast, isolated unit tests (120 files)
│   ├── integration/               # Docker-based (testcontainers)
│   │   └── containers/conftest.py # PostgreSQL, Redis, Langfuse containers
│   ├── feature/                   # End-to-end with real providers
│   ├── benchmark/                 # pytest-benchmark performance tests
│   ├── security/                  # Security test suite
│   └── mock_provider.py           # JSON-RPC mock MCP provider
│
├── docs/                          # Internal docs (synced to website at build)
│   ├── internal/PRODUCT_ARCHITECTURE.md  # Feature boundaries, cut list
│   └── cookbook/                   # 13 numbered recipes
│
├── pyproject.toml                 # Package config, pytest, ruff, mypy settings
├── Makefile                       # Python-specific targets
└── Dockerfile                     # Multi-stage: python:3.11 + hatch -> slim runtime
```

## Architecture

### Provider State Machine

```
COLD --> INITIALIZING --> READY --> DEGRADED --> DEAD
  ^                        |  ^        |
  |                        |  |        |
  +--- StopProvider -------+  +--------+
                               recovery
```

States managed by `Provider` aggregate root. Transitions emit domain events. Circuit breaker tracks consecutive failures.

### CQRS Flow

```
CLI/API request
    |
    v
CommandBus.dispatch(command)  -->  CommandHandler.handle()
    |                                    |
    v                                    v
Middleware pipeline              Provider aggregate mutates
(validation, tracing)            Events collected on aggregate
    |                                    |
    v                                    v
                                 EventBus.publish(events)
                                    |
                          +---------+---------+
                          |         |         |
                     Logging   Metrics    Audit
                     handler   handler    handler
```

### Event Sourcing

Aggregates rebuilt from event streams. Snapshots every 50 events for performance. Event upcasting handles schema evolution without migration scripts.

### Lock Hierarchy (Deadlock Prevention)

```python
PROVIDER = 10 < EVENT_BUS = 20 < EVENT_STORE = 30 < SAGA_MANAGER = 40 < STDIO_CLIENT = 50
```

Always acquire locks in ascending order. `TrackedLock` enforces this at runtime.

## Conventions

- **Ruff** for linting + formatting (120 char lines, Python 3.11 target)
- **Async-first**: asyncio mode auto, ASGI context vars for identity
- **Domain contracts own everything**: infrastructure implements `domain/contracts/` interfaces
- **Explicit event publishing**: handlers call `event_bus.publish()` after persistence
- **Value objects are frozen dataclasses**: immutable, validated at construction
- **Single-flight for cold starts**: 10 parallel tool calls to cold provider start it once
- **Hot-loading**: `RuntimeProviderStore` for dynamically loaded providers (separate from static config)
- **No DI framework**: explicit wiring in `server/bootstrap/` modules

## Enterprise Boundary

Core (`src/`) and enterprise (`enterprise/`) are **separate DDD boundaries**.

- Core NEVER imports from enterprise (CI-enforced)
- Enterprise depends on core interfaces
- Enterprise features conditionally bootstrapped via license validation
- Auth module has its own commands/queries/handlers/infrastructure

## Testing

- **pytest** + pytest-asyncio + pytest-cov + pytest-benchmark + pytest-timeout
- **Markers**: `benchmark`, `security`, `unit`, `integration`, `container`, `slow`, `stress`
- **Flags**: `--run-containers` (Docker tests), `--run-slow` (slow tests), `--container-runtime` (podman/docker)
- **Fixtures**: `temp_config_dir`, `mock_env`, `sample_provider_config`, `sample_tool_schema`
- **Testcontainers**: PostgreSQL, Redis, Langfuse, Prometheus (Ryuk disabled for Podman)
- **Mock provider**: `tests/mock_provider.py` implements JSON-RPC MCP protocol

## Complexity Hotspots

| File | Lines | Why it matters |
|------|-------|----------------|
| `domain/model/provider.py` | 1368 | Main aggregate: 51 methods, state machine + circuit breaker |
| `domain/events.py` | 1327 | 40+ event definitions (low complexity, good reference) |
| `errors.py` | 1234 | 30+ exception types, 150 conditional branches |
| `server/tools/batch/executor.py` | 901 | Batch execution, 8 methods averaging 90 lines each |
| `enterprise/auth/infrastructure/sqlite_store.py` | 856 | Event-sourced auth storage |

## What NOT to Do

- Do not import from `enterprise/` in `src/` code (CI rejects)
- Do not use `shell=True` in subprocess calls (security)
- Do not change lock acquisition order (deadlock)
- Do not call `_get_or_create_event_handler()` under external lock
- Do not start background workers during bootstrap
- Do not add tools that a broader scope removed (policy composition)
- Do not let automated discovery override static config (static always wins)
- Do not store raw API keys -- hash-only storage, return key once at creation
- Do not use emoji in code, comments, or documentation
- Do not rename or remove existing trace attributes
