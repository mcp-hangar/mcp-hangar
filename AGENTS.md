# mcp-hangar -- Python Core Platform

> DDD/CQRS runtime security and governance layer for MCP servers. Published to PyPI as `mcp-hangar`.

## Quick Reference

| Property | Value |
|----------|-------|
| Language | Python 3.11+ |
| Package manager | uv |
| Architecture | DDD + CQRS + Event Sourcing |
| License | MIT |
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
│   │   ├── model/                 # Aggregates: McpServer (1383 LOC), McpServerGroup, Tenant
│   │   │   ├── aggregate.py       # Base AggregateRoot with event collection
│   │   │   ├── mcp_server.py      # Main aggregate: state machine, health, circuit breaker
│   │   │   ├── event_sourced_mcp_server.py  # Rebuilds from event stream + snapshots
│   │   │   ├── mcp_server_group.py  # Load balancing, failover
│   │   │   └── circuit_breaker.py # Circuit breaker state machine
│   │   ├── contracts/             # 32 interfaces (Dependency Inversion Principle)
│   │   │   ├── command.py         # CommandHandler ABC
│   │   │   ├── event_bus.py       # IEventBus
│   │   │   ├── event_store.py     # IEventStore
│   │   │   ├── persistence.py     # IAuditRepository, IMcpServerConfigRepository
│   │   │   ├── authentication.py  # IAuthenticator
│   │   │   └── authorization.py   # IAuthorizer
│   │   ├── value_objects/         # Immutable domain primitives
│   │   │   ├── mcp_server.py      # McpServerState enum, McpServerMode, McpServerId
│   │   │   ├── config.py          # CommandLine, DockerImage, Endpoint
│   │   │   ├── security.py        # Principal, Role, Permission
│   │   │   └── capabilities.py    # McpServerCapabilities, ViolationType
│   │   ├── events.py              # 75+ domain events (1872 LOC)
│   │   ├── exceptions.py          # Domain-specific errors
│   │   ├── services/              # Domain services (stateless logic)
│   │   │   └── mcp_server_launcher/ # Subprocess/Docker launcher (shell=False enforced)
│   │   ├── security/              # Input validation, rate limiting
│   │   ├── discovery/             # Conflict resolution (static always wins)
│   │   └── repository.py          # IMcpServerRepository + InMemoryMcpServerRepository
│   │
│   ├── application/               # CQRS handlers + sagas
│   │   ├── commands/              # 30+ command types with handlers
│   │   │   ├── commands.py        # StartMcpServerCommand, InvokeToolCommand, etc.
│   │   │   ├── handlers.py        # Command handlers (BaseMcpServerHandler pattern)
│   │   │   ├── crud_commands.py   # Create/Update/Delete commands
│   │   │   └── crud_handlers.py   # CRUD handlers with DIP
│   │   ├── queries/               # Read-side queries + handlers
│   │   │   ├── queries.py         # ListMcpServersQuery, GetMcpServerQuery, etc.
│   │   │   └── handlers.py        # Return read models (flattened projections)
│   │   ├── event_handlers/        # 8 subscribers: logging, metrics, audit, security
│   │   ├── sagas/                 # Long-running transactions
│   │   │   ├── mcp_server_failover_saga.py   # 3-step failover with compensation
│   │   │   ├── mcp_server_recovery_saga.py   # Auto-restart degraded providers
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
│   │   │   ├── mcp_servers.py     # /api/mcp_servers CRUD
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
│   ├── auth/                      # Authentication and authorization
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
│   ├── compliance/                # Compliance reporting
│   ├── integrations/              # Third-party adapters (Langfuse, etc.)
│   ├── bootstrap/runtime.py       # Composition root (protocols + config)
│   └── facade.py                  # High-level API hiding complexity
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

### McpServer State Machine

```
COLD --> INITIALIZING --> READY --> DEGRADED --> DEAD
  ^                        |  ^        |
  |                        |  |        |
  +--- StopMcpServer ------+  +--------+
                               recovery
```

States managed by `McpServer` aggregate root. Transitions emit domain events. Circuit breaker tracks consecutive failures.

### CQRS Flow

```
CLI/API request
    |
    v
CommandBus.dispatch(command)  -->  CommandHandler.handle()
    |                                    |
    v                                    v
Middleware pipeline              McpServer aggregate mutates
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
- **Hot-loading**: `RuntimeMcpServerStore` for dynamically loaded providers (separate from static config)
- **No DI framework**: explicit wiring in `server/bootstrap/` modules

## Git Workflow for Agents

Read `https://github.com/mcp-hangar/docs/blob/main/development/GIT_FLOW.md` for the full flow. Hard rules for agent-authored PRs:

- **One PR, one goal, one Conventional Commit scope.** No mixed-scope changes.
- **Branch:** `<type>/<scope>-<slug>` where `<type>` is one of `feat|fix|perf|refactor|docs|test|build|ci|chore|revert|security`. Agent prefix `copilot/<task>-<slug>` is also valid.
- **PR title** is the Conventional Commit subject (squash-merge propagates it). Example: `feat(core): add capability validation cache`.
- **CC scopes** (enforced by CI): `core`, `enterprise`, `cli`, `operator`, `helm`, `ui`, `observability`, `security`, `docs`, `deps`, `release`, `infra`, `tests`, `repo`.
- **PR body** follows `.github/PULL_REQUEST_TEMPLATE.md` — fill every section including Agent metadata.
- **Soft LOC limit:** ≤400 lines changed (excluding tests). Larger changes require decomposition; open a parent issue first.
- **Issue first:** open or claim an `agent_task` issue before pushing the first commit. Link via `Closes #N` (or `Refs #N` for child PRs of an epic).
- **Forbidden paths for agent-authored PRs** (must be human-authored — see CODEOWNERS):
  - `src/mcp_hangar/server/security/`, `src/mcp_hangar/server/api/middleware*`
  - `https://github.com/mcp-hangar/docs/blob/main/adr/` (ADRs are human-authored; agents may draft content in issue comments)
  - `.github/workflows/release.yml`, `pyproject.toml` version field
- **CHANGELOG:** every non-trivial PR adds a line under `## [Unreleased]` in the appropriate section (`### Added`, `### Fixed`, `### Changed`, `### Security`). Trivial = `chore(deps)`, `ci`, `style`, `test`, pure `docs`.
- **No emoji** anywhere in code, comments, commit messages, or docs.
- **Squash-merge default.** Do not request merge commits.
- **Required status checks** (must pass before merge): `pr-validation / required-check`, `pr-title / validate`, `changelog / check`, `branch-name / validate`, `pr-body / validate`.

For ADR work specifically, see `docs/internal/ADR_AGENTS.md` — agents may draft ADR content in issue comments but never author the PR.

## Optional Modules

Auth, compliance, approvals, and integrations live under `src/mcp_hangar/` as first-class packages. Bootstrap uses try/except ImportError for graceful degradation if any module is removed or broken.

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
| `domain/model/mcp_server.py` | 1383 | Main aggregate: 51 methods, state machine + circuit breaker |
| `domain/events.py` | 1872 | 75+ event definitions (low complexity, good reference) |
| `errors.py` | 1248 | 30+ exception types, 150 conditional branches |
| `server/tools/batch/executor.py` | 925 | Batch execution, 8 methods averaging 90 lines each |
| `auth/infrastructure/sqlite_store.py` | 857 | Event-sourced auth storage |

## What NOT to Do

- Do not use `shell=True` in subprocess calls (security)
- Do not change lock acquisition order (deadlock)
- Do not call `_get_or_create_event_handler()` under external lock
- Do not start background workers during bootstrap
- Do not add tools that a broader scope removed (policy composition)
- Do not let automated discovery override static config (static always wins)
- Do not store raw API keys -- hash-only storage, return key once at creation
- Do not use emoji in code, comments, or documentation
- Do not rename or remove existing trace attributes
