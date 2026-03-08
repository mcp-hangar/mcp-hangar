# Technology Stack: v1.0 Production Hardening

**Project:** MCP Hangar
**Researched:** 2026-03-08
**Scope:** Stack additions/changes needed for v1.0 production hardening features ONLY

## Existing Stack (Not Re-Researched)

Already validated and in place: Python 3.11+, DDD + CQRS + Event Sourcing, thread-based concurrency with RLock/TrackedLock hierarchy, domain events on all state changes, Provider state machine (COLD/INITIALIZING/READY/DEGRADED/DEAD), SQLite and in-memory event store backends, structlog for structured logging, Prometheus metrics, ruff + black + isort for code quality, pytest + pytest-asyncio + pytest-cov + pytest-timeout for testing, hypothesis>=6.90.0 (already in dev dependencies), ruff>=0.3.0 (already in dev dependencies), mypy>=1.8.0 (already in dev dependencies), existing retry.py with BackoffStrategy (exponential/linear/constant), existing InputValidator with command blocklist/allowlist, existing CircuitBreaker (in-memory only), existing SagaManager with EventTriggeredSaga pattern.

---

## Recommendation Summary

**No new third-party dependencies are needed.** All seven hardening features can be implemented using the existing stack: Python stdlib, SQLite (already a dependency), the existing `hypothesis` dev dependency, and the project's own infrastructure primitives (`TrackedLock`, `SQLiteConnectionFactory`, `MigrationRunner`, `EventSerializer`, `BackoffStrategy`).

This is the correct outcome for a hardening milestone. Adding new dependencies during a stability-focused phase would be counterproductive.

---

## Feature-by-Feature Stack Analysis

### 1. Saga Persistence with Checkpoint/Resume

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| sqlite3 (stdlib) | Python 3.11+ built-in | Saga state persistence | Already used for event store. Reuse `SQLiteConnectionFactory` and `MigrationRunner` from `infrastructure/persistence/database_common.py`. No new dependency. |

**What exists today:**

- `SagaManager` in `infrastructure/saga_manager.py` stores all state in `_active_sagas: dict[str, Saga]` and `_retry_state: dict[str, dict]` -- pure in-memory, lost on restart.
- `SagaContext` already has `to_dict()` for serialization and tracks `saga_id`, `correlation_id`, `state`, `current_step`, `data`, and `error`.
- `SagaState` enum already has `RUNNING`, `COMPLETED`, `COMPENSATING`, `COMPENSATED`, `FAILED` states.

**What to build (no new deps):**

- `ISagaRepository` contract in `domain/contracts/` (port interface).
- `SQLiteSagaRepository` in `infrastructure/persistence/` using existing `SQLiteConnectionFactory`.
- Schema migration via existing `MigrationRunner` for `saga_checkpoints` table: `(saga_id, saga_type, correlation_id, state, current_step, data_json, created_at, updated_at)`.
- Idempotency keys via `correlation_id` (already on `SagaContext`).
- Modify `SagaManager._handle_event()` to checkpoint after each step and command execution.
- Recovery: on startup, query incomplete sagas (`state IN ('running', 'compensating')`) and resume from `current_step`.

**Why no ORM or external library:**

- The saga checkpoint table is simple (single table, no joins, no complex queries).
- SQLite WAL mode (already configured) provides the concurrency needed.
- Adding SQLAlchemy or similar would be a new dependency for a table with 4 queries (INSERT, UPDATE, SELECT by state, DELETE completed).

### 2. Circuit Breaker State Persistence

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| sqlite3 (stdlib) | Python 3.11+ built-in | Circuit breaker state persistence | Same SQLite infrastructure as saga persistence. Single additional table. |

**What exists today:**

- `CircuitBreaker` in `domain/model/circuit_breaker.py` is pure in-memory with `_state`, `_failure_count`, `_opened_at` fields.
- Already has `to_dict()` for serialization.
- Uses `threading.Lock` (not `TrackedLock` -- should be upgraded as part of this milestone).

**What to build (no new deps):**

- `ICircuitBreakerStore` contract in `domain/contracts/`.
- `SQLiteCircuitBreakerStore` in `infrastructure/persistence/` -- simple key-value: `(provider_id, state, failure_count, opened_at, updated_at)`.
- Load on `CircuitBreaker.__init__()` or factory method.
- Persist on state transitions only (`_open()`, `_close()`) -- not on every `record_failure()` (too frequent; batch the failure count update on a timer or on state change).
- Schema migration via existing `MigrationRunner`.

**Design decision: persist on state change, not per-failure.**
Circuit breaker state changes are infrequent (CLOSED->OPEN, OPEN->CLOSED). Persisting on every `record_failure()` would add unnecessary I/O. Instead, persist the `failure_count` alongside state transitions and on graceful shutdown. On restart, a circuit breaker that was OPEN with an expired timeout will correctly transition to CLOSED on the first `allow_request()` call -- the existing `_should_reset()` logic handles this.

### 3. Event Store Snapshots for Fast Aggregate Replay

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| sqlite3 (stdlib) | Python 3.11+ built-in | Snapshot persistence | Same database as event store. Additional `snapshots` table co-located with `events` table. |
| json (stdlib) | Python 3.11+ built-in | Snapshot serialization | Aggregate state serialized to JSON, matching event serialization pattern. |

**What exists today:**

- `IEventStore` contract has `read_stream(stream_id, from_version)` -- already supports reading from a specific version, which is the core primitive for snapshot-based replay.
- `SQLiteEventStore` handles serialization via `EventSerializer`.
- `AggregateRoot` base class has `_version` tracking.
- No snapshot infrastructure exists.

**What to build (no new deps):**

- `ISnapshotStore` contract in `domain/contracts/`: `save_snapshot(stream_id, version, state_json)`, `load_snapshot(stream_id) -> (version, state_json) | None`.
- `SQLiteSnapshotStore` co-located with `SQLiteEventStore` -- same database file, additional `snapshots` table: `(stream_id PRIMARY KEY, version, state_data, created_at)`.
- `SnapshotAggregateRepository` pattern: load snapshot -> replay events from `snapshot_version + 1` -> return hydrated aggregate.
- Snapshot frequency policy: snapshot every N events per stream (configurable, default 100) or when replay time exceeds threshold.
- Background snapshot worker in `gc.py` alongside existing GC and health check workers.

**Why co-locate with event store database:**

- Snapshot reads always co-occur with event reads (load snapshot, then replay remaining events).
- Single transaction boundary for consistency.
- No need for a separate database connection or file.

**Serialization approach:** Reuse `EventSerializer._json_encoder` for consistent datetime/value-object handling. Aggregate `to_dict()` methods (already present on `CircuitBreaker`, `HealthTracker`) provide the serialization surface. `Provider` will need a `to_snapshot_dict()` / `from_snapshot_dict()` pair.

### 4. Property-Based Testing with Hypothesis

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| hypothesis | >=6.90.0 | Property-based testing | **Already in dev dependencies** in `pyproject.toml`. Already used in `tests/unit/observability/test_property_based.py`. No change needed. |

**What exists today:**

- `hypothesis>=6.90.0` is already in `[project.optional-dependencies].dev`.
- One property-based test file exists: `tests/unit/observability/test_property_based.py` (292 lines) testing TraceContext, NullObservabilityAdapter, and LangfuseConfig.
- Uses `@given`, `@settings`, `st.text`, `st.integers`, `st.floats`, `st.dictionaries`, `st.one_of`, `st.none()`.

**What to build (no new deps):**

- `hypothesis.stateful.RuleBasedStateMachine` for Provider state machine testing: define rules for each valid transition, invariant checks (e.g., only READY providers have tools, consecutive_failures resets on success).
- State machine strategies: `ProviderState` enum strategy, `DomainEvent` strategy generating valid events.
- Property tests for `CircuitBreaker`: invariant that `failure_count < threshold` implies `state == CLOSED`.
- Property tests for `HealthTracker`: `backoff` monotonically increases with `consecutive_failures` up to cap.
- Property tests for `TokenBucket` rate limiter: tokens never exceed capacity, refill rate is bounded.

**Key hypothesis features to leverage:**

- `hypothesis.stateful.RuleBasedStateMachine` -- ideal for Provider state machine, tests all reachable state combinations.
- `@settings(max_examples=200, stateful_step_count=50)` -- tune for CI speed.
- `@settings(suppress_health_check=[HealthCheck.too_slow])` -- already used in existing tests.
- Profile configuration in `conftest.py`: `settings.register_profile("ci", max_examples=50)` for fast CI, `settings.register_profile("dev", max_examples=500)` for thorough local testing.

### 5. Exponential Backoff Health Checks

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| (none -- stdlib only) | -- | Backoff calculation | `HealthTracker._calculate_backoff()` already exists. `BackoffStrategy` and `calculate_backoff()` in `retry.py` provide the full algorithm with jitter. No new dependency. |

**What exists today:**

- `HealthTracker._calculate_backoff()` uses `min(60.0, 2**consecutive_failures)` -- basic exponential backoff.
- `HealthTracker.can_retry()` checks if backoff has elapsed.
- `BackgroundWorker` in `gc.py` runs health checks at a fixed interval (`interval_s`) regardless of provider state.
- `retry.py` has `calculate_backoff()` with full support for exponential/linear/constant strategies, jitter, and max delay cap.

**What to change (no new deps):**

- Extend `HealthTracker` to support configurable backoff parameters: `initial_interval`, `max_interval`, `multiplier`, `jitter_factor`.
- Make `BackgroundWorker` health check frequency **per-provider** based on provider state:
  - `READY`: Normal interval (e.g., 10s).
  - `DEGRADED`: Exponential backoff using `HealthTracker.time_until_retry()`.
  - `COLD`/`DEAD`: Skip health checks entirely (no point checking a stopped provider).
- Reuse `calculate_backoff()` from `retry.py` inside `HealthTracker._calculate_backoff()` for consistency and jitter support.

### 6. Command Validation/Allowlisting for Discovery Sources

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| (none -- stdlib only) | -- | Command allowlisting | `InputValidator` in `domain/security/input_validator.py` already has `allowed_commands`, `blocked_commands`, `validate_command()`, and `DANGEROUS_PATTERNS`. Just needs integration with discovery sources. |

**What exists today:**

- `InputValidator` has full command validation with blocklist (rm, sudo, bash, curl, etc.) and optional allowlist.
- `DockerDiscoverySource._parse_container()` at line 205 does `connection_info["command"] = cmd.split()` from Docker labels **with no validation**.
- `FilesystemDiscoverySource` reads commands from YAML files -- also unvalidated.
- `EntrypointDiscoverySource` resolves Python package entrypoints -- lower risk but still unvalidated.

**What to change (no new deps):**

- Create `DiscoveryCommandValidator` (or reuse `InputValidator` directly) that validates commands before they become `DiscoveredProvider` objects.
- Add validation in `DiscoverySource.on_provider_discovered()` (the base class hook) so ALL discovery sources get command validation.
- Configuration: `allowed_discovery_commands` in YAML config to explicitly allowlist what commands can be auto-started from discovery.
- Default policy: **deny-by-default** for discovery sources (discovery-sourced commands require explicit allowlisting), **allow-by-default** for static config (admin wrote the config).
- This is a **policy** change, not a stack change. The validation primitives already exist.

### 7. Rate Limiter as Application-Layer Middleware

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| (none -- stdlib only) | -- | Middleware pattern | `InMemoryRateLimiter` and `CompositeRateLimiter` already exist in `domain/security/rate_limiter.py`. `check_rate_limit()` is called from `server/validation.py`. Just needs restructuring. |

**What exists today:**

- `InMemoryRateLimiter` with `TokenBucket` algorithm in `domain/security/rate_limiter.py`.
- `CompositeRateLimiter` for combining global + per-provider limits.
- `check_rate_limit()` called directly in tool handlers via `server/validation.py`.
- `RateLimitResult.to_headers()` generates standard rate limit headers.
- Global singleton `_global_limiter` -- anti-pattern per project constraints ("Don't use global mutable state, use DI").

**What to change (no new deps):**

- Move rate limiting from per-tool-handler calls to an application-layer middleware/interceptor pattern.
- Create `RateLimitMiddleware` in `application/middleware/` that wraps command/query bus execution.
- Alternatively, use a decorator on `CommandBus.send()` and `QueryBus.execute()` -- transport-agnostic by definition since CQRS buses are the single entry point.
- Remove global singleton `_global_limiter`. Inject `RateLimiter` via bootstrap composition root.
- `RateLimitConfig` scoping: extract rate limit key from command/query metadata (provider_id, client_id, or global).

---

## What NOT to Add

| Technology | Reason Not to Add |
|------------|-------------------|
| SQLAlchemy / ORM | Saga and circuit breaker tables are trivially simple (single table, 3-4 queries each). ORM adds dependency weight, migration tooling complexity, and import time for no value. |
| Redis | Distributed rate limiting / persistence is explicitly out of scope ("Single-node first, scale later" -- PROJECT.md). SQLite provides the persistence needed for single-node. |
| Alembic | Schema migrations are simple (`CREATE TABLE IF NOT EXISTS`). The existing `MigrationRunner` in `database_common.py` handles versioned migrations already. |
| tenacity | The project already has a comprehensive `retry.py` module with `BackoffStrategy`, `calculate_backoff()`, `RetryPolicy`, `@with_retry`, and `@with_retry_async`. Adding tenacity would be redundant. |
| circuitbreaker (PyPI) | The project has its own `CircuitBreaker` implementation tailored to the domain model. An external library wouldn't integrate with the event sourcing pattern or lock hierarchy. |
| pytest-hypothesis-integration | No such thing is needed. `hypothesis` integrates directly with pytest via `@given` decorator. Already working in the codebase. |
| msgpack / pickle | For snapshot serialization. JSON is sufficient for aggregate state, matches event serialization approach, and is human-debuggable. Binary formats add complexity for marginal performance gain on small aggregates. |
| APScheduler / schedule | For backoff-based health check scheduling. The existing `BackgroundWorker._loop()` with `time.sleep(interval_s)` pattern can be extended with per-provider next-check timestamps. A full scheduler library is overkill. |

---

## Existing Dependencies to Leverage (Not Add)

These are already in `pyproject.toml` and should be used -- not duplicated or replaced:

| Dependency | Use For Hardening |
|------------|-------------------|
| `sqlite3` (stdlib) | Saga persistence, circuit breaker persistence, snapshot store |
| `threading` (stdlib) | `TrackedLock` for new stores, `threading.Event` for graceful shutdown |
| `json` (stdlib) | Snapshot serialization, saga data serialization |
| `dataclasses` (stdlib) | Saga checkpoint, snapshot, circuit breaker state dataclasses |
| `hypothesis>=6.90.0` (dev dep) | `RuleBasedStateMachine` for state machine property tests |
| `ruff>=0.3.0` (dev dep) | Enable `BLE001` rule for bare-except enforcement |
| `mypy>=1.8.0` (dev dep) | Gradual strictness increase (enable `check_untyped_defs`) |
| `pytest>=8.0.0` (dev dep) | Test infrastructure for all new tests |

---

## Configuration Changes Needed

### ruff Configuration (pyproject.toml)

```toml
[tool.ruff.lint]
select = ["E", "F", "N", "W", "UP", "B", "C4", "SIM", "BLE"]
# Add BLE to select list to enable bare-except detection
# BLE001: Do not catch blind exception: `Exception`
# Remove B904 from ignore list to enforce `raise ... from err`
```

**Current state:** `BLE` (blind exception) rules are NOT in the `select` list. `B904` (`raise ... from err`) is explicitly ignored. Both should be enabled for exception hygiene.

### mypy Configuration (pyproject.toml)

```toml
[tool.mypy]
# Gradually enable (one at a time, fix issues, then enable next):
check_untyped_defs = true  # Step 1: catch type errors in untyped functions
no_implicit_optional = true  # Step 2: explicit Optional[X] / X | None
disallow_incomplete_defs = true  # Step 3: require full signatures
```

**Current state:** All three are `false`. Enable incrementally during the code quality phase.

---

## Schema Additions (SQLite)

All three new tables use the same database file as the event store (`data/events.db`), managed via `MigrationRunner`:

### Saga Checkpoints Table

```sql
CREATE TABLE IF NOT EXISTS saga_checkpoints (
    saga_id TEXT PRIMARY KEY,
    saga_type TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    state TEXT NOT NULL,  -- SagaState enum value
    current_step INTEGER NOT NULL DEFAULT 0,
    data_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_saga_checkpoints_state
ON saga_checkpoints(state);

CREATE INDEX IF NOT EXISTS idx_saga_checkpoints_type
ON saga_checkpoints(saga_type, state);
```

### Circuit Breaker State Table

```sql
CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    provider_id TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'closed',  -- CircuitState enum value
    failure_count INTEGER NOT NULL DEFAULT 0,
    opened_at REAL,  -- Unix timestamp, nullable
    updated_at TEXT NOT NULL
);
```

### Event Snapshots Table

```sql
CREATE TABLE IF NOT EXISTS snapshots (
    stream_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    state_data TEXT NOT NULL,  -- JSON
    created_at TEXT NOT NULL
);
```

---

## Integration Points

### With Existing Event Store

- Snapshot store uses same `SQLiteConnectionFactory` instance.
- `read_stream(stream_id, from_version=snapshot_version + 1)` already works -- no event store changes needed.
- Snapshot creation runs as a background worker alongside GC and health check workers in `gc.py`.

### With Existing Bootstrap

- All new stores (`SQLiteSagaRepository`, `SQLiteCircuitBreakerStore`, `SQLiteSnapshotStore`) wired in `server/bootstrap/__init__.py`.
- Injected via constructor into `SagaManager`, `CircuitBreaker` factory, and aggregate repositories.
- Follow existing pattern: `NullSagaRepository`, `NullCircuitBreakerStore`, `NullSnapshotStore` null objects for when persistence is disabled.

### With Existing Lock Hierarchy

- New stores acquire locks at `LockLevel.REPOSITORY` (31) -- same as existing repositories.
- `SagaManager` already uses `LockLevel.SAGA_MANAGER` (40).
- No lock hierarchy changes needed.

### With Existing Command/Query Bus

- Rate limiter middleware wraps `CommandBus.send()` and `QueryBus.execute()`.
- Intercepts before handler execution -- consistent with CQRS pattern.
- No changes to handler signatures.

---

## Sources

| Source | Confidence | What It Verified |
|--------|------------|------------------|
| `packages/core/pyproject.toml` | HIGH | Current dependencies: hypothesis>=6.90.0 already in dev deps, ruff config, mypy config |
| `infrastructure/persistence/database_common.py` | HIGH | `SQLiteConnectionFactory`, `MigrationRunner`, `SQLiteConfig` already exist |
| `infrastructure/persistence/sqlite_event_store.py` | HIGH | `read_stream(from_version)` supports snapshot-based replay pattern |
| `infrastructure/saga_manager.py` | HIGH | `SagaContext.to_dict()`, `SagaState` enum, in-memory-only storage confirmed |
| `domain/model/circuit_breaker.py` | HIGH | In-memory-only, `to_dict()` exists, `threading.Lock` (not TrackedLock) |
| `domain/security/input_validator.py` | HIGH | `InputValidator` with `allowed_commands`, `blocked_commands`, `validate_command()` |
| `domain/security/rate_limiter.py` | HIGH | `InMemoryRateLimiter`, `CompositeRateLimiter`, `TokenBucket`, global singleton anti-pattern |
| `domain/model/health_tracker.py` | HIGH | `_calculate_backoff()` exists, basic `min(60, 2^n)` formula |
| `retry.py` | HIGH | Full `calculate_backoff()` with strategies, jitter, max_delay |
| `gc.py` | HIGH | `BackgroundWorker` fixed-interval pattern, health check loop |
| `tests/unit/observability/test_property_based.py` | HIGH | Hypothesis already used, patterns established |
| `infrastructure/lock_hierarchy.py` | HIGH | `LockLevel.REPOSITORY` (31) available for new stores |
| Python 3.11 stdlib docs | HIGH | `sqlite3`, `json`, `threading`, `dataclasses` capabilities confirmed |
