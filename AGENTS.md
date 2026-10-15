# mcp-hangar -- Python Core Platform

> DDD/CQRS runtime security and governance layer for MCP servers. Published to PyPI as `mcp-hangar`.

## Quick Reference

| Property | Value |
|----------|-------|
| Language | Python 3.11+ |
| Package manager | uv |
| Architecture | DDD + CQRS; event sourcing for the auth stores |
| License | MIT |
| Entry point | `mcp-hangar serve` or `python -m mcp_hangar.server` |

## Commands

```bash
# Setup
uv sync
git config blame.ignoreRevsFile .git-blame-ignore-revs  # skip bulk reformats in git blame

# Test
uv run pytest tests/ -x -q
uv run pytest tests/ -x -q -n auto --dist loadfile -m "not benchmark"  # parallel, as CI runs it
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
│   │   │   ├── event_sourced_api_key.py       # Auth aggregates rebuilt from an event stream
│   │   │   ├── event_sourced_role_assignment.py
│   │   │   ├── mcp_server_group.py  # Load balancing, failover
│   │   │   └── circuit_breaker.py # Circuit breaker state machine
│   │   ├── contracts/             # 30 interfaces (Dependency Inversion Principle)
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
│   │   ├── events/                # Domain events by area (lifecycle, invocation, approvals, auth, ...)
│   │   ├── exceptions.py          # Domain-specific errors
│   │   ├── services/              # Domain services (stateless logic)
│   │   ├── policies/              # Policy DSL, L7 egress, header exposure, health policy
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
│   │   ├── event_handlers/        # Subscribers: logging, audit, cost, security, projections, detection
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
│   │   ├── saga_manager.py        # Saga orchestration + state persistence
│   │   ├── single_flight.py       # Cold start deduplication
│   │   ├── persistence/           # SQLite/Postgres repositories, event stores, UoW, event upcasting
│   │   ├── discovery/             # 4 adapters: Kubernetes, Docker, filesystem, entrypoint
│   │   ├── launchers/             # Subprocess/Docker/HTTP launchers (shell=False enforced)
│   │   ├── identity/              # JWT/header identity extraction
│   │   ├── observability/         # OTLP audit export
│   │   └── truncation/            # Response truncation caching (memory/Redis)
│   │
│   ├── server/                    # HTTP/WebSocket/CLI/MCP protocol
│   │   ├── api/                   # FastAPI REST endpoints
│   │   │   ├── router.py          # Route registration
│   │   │   ├── mcp_servers.py     # /api/mcp_servers CRUD
│   │   │   ├── tools.py           # Tool invocation
│   │   │   ├── middleware.py      # Auth context, error handling, CORS
│   │   │   └── ws/                # WebSocket real-time events
│   │   ├── cli/                   # Typer CLI (serve, init, add, remove, status, pin, auth, config, completion)
│   │   ├── bootstrap/             # Composition root (one module per concern)
│   │   │   ├── cqrs.py            # Register all command/query handlers
│   │   │   ├── event_handlers.py  # Wire event subscribers
│   │   │   ├── composition.py     # Shared composition state and runtime accessors
│   │   │   └── workers.py         # Background task workers
│   │   └── tools/                 # MCP tool implementations
│   │       └── batch/             # Batch executor with concurrency control
│   │
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
│   ├── bootstrap/runtime.py       # Composition root (protocols + config)
│   ├── lock_hierarchy.py          # Deadlock prevention (lock ordering)
│   └── facade.py                  # High-level API hiding complexity
│
├── tests/
│   ├── unit/                      # Fast, isolated unit tests
│   ├── integration/               # In-process end-to-end (no container runtime)
│   ├── live/                      # Black-box tiers against a running gateway
│   ├── acceptance/                # Deployment acceptance: kubectl + bash, not pytest
│   ├── benchmark/                 # pytest-benchmark performance tests
│   ├── conformance/baseline.yml   # Expected failures for the official MCP suite
│   └── mock_provider.py           # JSON-RPC mock MCP provider
│
├── docs/                          # Internal docs (synced to website at build)
│   ├── internal/PRODUCT_ARCHITECTURE.md  # Feature boundaries, cut list
│   ├── internal/LIVE_VERIFICATION.md     # What must be verified over a real transport
│   └── internal/ADR_AGENTS.md            # How agents work with ADRs
│
├── pyproject.toml                 # Package config, pytest, ruff, mypy settings
├── Makefile                       # Python-specific targets
└── Dockerfile                     # Multi-stage: python:3.14 + hatch -> slim runtime
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

The `McpServer` aggregate is not event-sourced: its state lives in memory and its events go to the event bus and, when an event store is configured, to it. Event sourcing is used where state is rebuilt from a stream: the auth stores (`auth/infrastructure/event_sourced_store.py`, snapshot every 50 events) and the `event_sourced_api_key` / `event_sourced_role_assignment` aggregates. Event upcasting (`infrastructure/persistence/event_upcaster.py` and `upcasters/`) handles schema evolution without migration scripts.

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
- **CC scopes** (enforced by CI; the list is in `.github/workflows/pr-title.yml`): `core`, `cli`, `ci`, `operator`, `helm`, `ui`, `observability`, `security`, `docs`, `deps`, `deps-dev`, `release`, `infra`, `tests`, `repo`.
- **PR body** follows `.github/PULL_REQUEST_TEMPLATE.md` — fill every section including Agent metadata.
- **Soft LOC limit:** ≤400 lines changed (excluding tests). Larger changes require decomposition; open a parent issue first.
- **Issue first:** open or claim an `agent_task` issue before pushing the first commit. Link via `Closes #N` (or `Refs #N` for child PRs of an epic).
- **Forbidden paths for agent-authored PRs** (must be human-authored — see CODEOWNERS):
  - `src/mcp_hangar/server/api/middleware*`
  - `https://github.com/mcp-hangar/docs/blob/main/adr/` (ADRs are human-authored; agents may draft content in issue comments)
  - `.github/workflows/release.yml`, `pyproject.toml` version field
- **CHANGELOG:** every non-trivial PR adds **one new file** `changelog.d/<id>-<slug>.<kind>.md`, where `<kind>` is `added`, `changed`, `deprecated`, `removed`, `fixed` or `security`. Never edit `CHANGELOG.md` — it is assembled from the fragments at release time, and an entry written there directly is overwritten. See `changelog.d/README.md`. Trivial = `chore(deps)`, `ci`, `style`, `test`, pure `docs`.
- **UPGRADE:** a change a reader has to act on adds **one new file** `upgrade.d/<id>-<slug>.md` whose first line is `### <headline>`, followed by the note's body. Never edit `UPGRADE.md` — the release folds the fragments into `## Upgrade to <version>` and deletes them; a `## Next` section written there is the conflict the fragments exist to avoid. See `upgrade.d/README.md`.
- **No emoji** anywhere in code, comments, commit messages, or docs.
- **Never `!` or `BREAKING CHANGE:` in a commit.** `3.x` is reserved for a business milestone, so release-please must never compute a major. A change that breaks callers ships as a **minor** with an upgrade note in `upgrade.d/` naming the old and new form -- which is what 2.2.0 and 2.3.0 did. If a major is genuinely wanted, that is a human decision, not a commit footer.
- **Squash-merge default.** Do not request merge commits.
- **Required status checks** (must pass before merge; the repository ruleset is the source): `required-check` (PR Validation), `check` (changelog), `pr-title / validate`, `branch-name / validate`, `pr-body / validate`, `lint`, `test (3.11)`, `test (3.12)`, `test (3.13)`, `test (3.14)`, `decision-coverage`, `integration`, `integration-newest`, `build`.

For ADR work specifically, see `docs/internal/ADR_AGENTS.md` — agents may draft ADR content in issue comments but never author the PR.

## Optional Modules

Auth, compliance, and approvals live under `src/mcp_hangar/` as first-class packages. Bootstrap uses try/except ImportError for graceful degradation if any module is removed or broken.

- Auth module has its own commands/queries/handlers/infrastructure

## Testing

- **pytest** + pytest-asyncio + pytest-cov + pytest-benchmark + pytest-timeout
- **Markers**: `benchmark`, `security`, `live`, `t0`/`t1`/`t2` -- all registered in `pyproject.toml`
- **No opt-in flags**: a test that only runs behind a flag nobody passes does not run. The
  `--run-containers` / `--run-slow` tiers and their testcontainers fixtures were deleted for
  exactly that reason; anything needing a real runtime belongs in `tests/live` (nightly) or the lab.
- **A control is tested through the path production takes.** A test that asserts a security or governance control fires (a gate, a policy, a scope check, a suspension, an approval) must reach it through `bootstrap()` or the served ASGI/stdio app, not by constructing the component. Direct construction is fine for a pure unit test of the component's own logic, and such a test does not count as coverage of the control. The recurring defect family "mechanism present, path absent" is a test that built the object through a path production never takes. The pattern is `tests/unit/test_security_predicates_are_reachable.py`: a real request down each served path, with a spy that fails when a registered predicate does not run.
- **Mock provider**: `tests/mock_provider.py` implements JSON-RPC MCP protocol
- **Acceptance**: `tests/acceptance/ha_two_gateways.sh` is run by hand against a cluster you own
  (it kills a pod). Not pytest on purpose -- it tests the deployment, which is how it found that
  the shipped image had no PostgreSQL driver. Apply the manifests beside it first.
- **Security tests** live in `tests/unit` like everything else, marked `@pytest.mark.security`.
  The marker is the category; a separate directory for two files was not.

## Complexity Hotspots

Line counts are approximate (`wc -l`); they drift, the files do not move often.

| File | Lines | Why it matters |
|------|-------|----------------|
| `server/tools/batch/executor.py` | ~3000 | Batch execution: gates, concurrency, approvals, cancellation |
| `domain/model/mcp_server.py` | ~2700 | Main aggregate: state machine, health, circuit breaker |
| `metrics.py` | ~2100 | Every Prometheus metric and its recording helper |
| `server/config.py` | ~2100 | Config loading: servers, groups, policies, pins |
| `domain/exceptions.py` | ~1000 | The exception hierarchy (~50 types) |
| `domain/events/` | ~2400 in all | ~90 event definitions, one module per area (low complexity, good reference) |

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
