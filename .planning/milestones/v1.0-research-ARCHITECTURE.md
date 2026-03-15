# Architecture Patterns

**Domain:** Production hardening for MCP Hangar Python core (v1.0)
**Researched:** 2026-03-08
**Confidence:** HIGH (based on deep analysis of existing codebase -- all components read and traced)

## System Overview

The v1.0 hardening milestone integrates 11 features into an existing DDD/CQRS/Event Sourcing architecture. The core challenge is that most features cut across multiple architectural layers -- saga persistence touches domain contracts, infrastructure persistence, and bootstrap wiring. The architecture must absorb these changes without violating layer boundaries (domain has NO external dependencies, layer flow is inward only).

### Existing Architecture (Relevant Layers)

```text
domain/
    model/
        aggregate.py          -- AggregateRoot base (events, versioning)
        provider.py           -- Provider aggregate root (state machine, tools, health)
        circuit_breaker.py    -- CircuitBreaker (in-memory only, no persistence)
        health_tracker.py     -- HealthTracker entity (backoff, failure counting)
        event_sourced_provider.py  -- EventSourcedProvider + ProviderSnapshot
    contracts/
        event_store.py        -- IEventStore interface (domain-level)
    security/
        rate_limiter.py       -- RateLimiter ABC, InMemoryRateLimiter, TokenBucket
        input_validator.py    -- Validation for provider_id, tool_name, arguments
    discovery/
        discovered_provider.py -- DiscoveredProvider value object
    events.py                 -- All domain events (40+ event types)

application/
    sagas/
        provider_recovery_saga.py   -- EventTriggeredSaga (in-memory retry state)
        provider_failover_saga.py   -- EventTriggeredSaga
        group_rebalance_saga.py     -- EventTriggeredSaga
    commands/                       -- Command definitions + handlers

infrastructure/
    saga_manager.py          -- SagaManager (in-memory only, no persistence)
    event_store.py           -- InMemoryEventStore, FileEventStore, EventStoreSnapshot
    event_bus.py             -- EventBus with IEventStore integration
    event_sourced_repository.py  -- EventSourcedProviderRepository
    lock_hierarchy.py        -- TrackedLock with LockLevel enum
    discovery/
        docker_source.py     -- DockerDiscoverySource (no retry/reconnect)

server/
    bootstrap/               -- Composition root (wiring)
    validation.py            -- Rate limit check, input validation
    context.py               -- ApplicationContext (DI container)
```

### Key Architectural Properties

1. **Lock hierarchy**: Provider(10) -> EventBus(20) -> EventStore(30) -> SagaManager(40) -> StdioClient(50)
2. **Event sourcing**: AggregateRoot._record_event() -> collect_events() -> EventBus.publish_aggregate_events()
3. **Copy-reference-under-lock pattern**: Copy client ref under lock, do I/O outside
4. **Singleton singletons**: Global instances via get_*() functions with set_*() for DI
5. **Thread-based concurrency**: No asyncio in domain -- threading.Lock/RLock throughout

## Feature-by-Feature Architecture Integration

### Feature 1: Concurrency Safety (P0)

**Problem:** I/O operations performed while holding Provider._lock; request-response race in StdioClient where responses can arrive for requests not yet registered.

**Integration Points:**

| Layer | Component | Change Type | Description |
|-------|-----------|-------------|-------------|
| Domain | `provider.py` | MODIFY | Audit all lock-holding paths; ensure _start() client creation and_perform_mcp_handshake() follow copy-ref pattern consistently |
| Domain | `provider.py` | MODIFY | The _refresh_tools() call inside invoke_tool() holds the lock during client.call() -- must extract |
| Infrastructure | `stdio_client.py` | MODIFY | Fix request-response race: register PendingRequest BEFORE writing to stdin, not after |
| Infrastructure | `lock_hierarchy.py` | UNCHANGED | Hierarchy already correct; TrackedLock validation catches violations at runtime |

**Data Flow Change:**

```text
BEFORE: invoke_tool() -> [lock] -> ensure_ready() -> _refresh_tools() -> client.call() [I/O UNDER LOCK] -> [unlock]
AFTER:  invoke_tool() -> [lock] -> ensure_ready() -> copy client ref -> [unlock] -> client.call() -> [lock] -> update state -> [unlock]
```

**StdioClient Race Fix:**

```text
BEFORE: write request to stdin -> [pending_lock] -> register pending -> [unlock] -> reader matches response
AFTER:  [pending_lock] -> register pending -> [unlock] -> write request to stdin -> reader matches response
```

This ensures the reader thread can always find the PendingRequest for any response it receives.

**Architectural Note:** The _start() method currently does I/O (process creation, MCP handshake) while holding Provider._lock. This is partially intentional (prevents concurrent starts) but could be refactored to use a per-provider start lock or guard flag instead. However, for v1.0, the pragmatic fix is to ensure _start() is the ONLY path that does heavy I/O under lock, and it only blocks that specific provider instance.

---

### Feature 2: Exception Hygiene (P0)

**Problem:** Bare `except` catches that swallow errors silently, violating "fail fast, fail loud" principle.

**Integration Points:**

| Layer | Component | Change Type | Description |
|-------|-----------|-------------|-------------|
| ALL | Multiple files | MODIFY | Replace bare `except:` and `except Exception:` with specific catches |
| Config | `pyproject.toml` or `ruff.toml` | MODIFY | Enable ruff rule BLE001 (blind exception) |
| Domain | `provider.py` | MODIFY | Lines like `except Exception: pass` in _begin_cold_start_tracking, _end_cold_start_tracking |
| Infrastructure | `event_bus.py` | MODIFY | Handler error catch is broad but logs -- acceptable; add type narrowing |

**Pattern to Follow:**

```python
# BEFORE (violation)
except Exception:
    pass

# AFTER (specific catch, logged)
except (ConnectionError, TimeoutError) as e:
    logger.warning("metrics_publish_failed", error=str(e), provider_id=self.provider_id)
```

**No architectural changes needed.** This is a code quality sweep -- no new components, no data flow changes.

---

### Feature 3: Command Injection Prevention (P0)

**Problem:** Discovery sources (Docker, filesystem) provide command strings that are executed as subprocess commands. No validation against allowlisted binaries.

**Integration Points:**

| Layer | Component | Change Type | Description |
|-------|-----------|-------------|-------------|
| Domain | `security/` | NEW | `command_validator.py` -- allowlist of safe executables, validation rules |
| Domain | `contracts/` | MODIFY | Add command validation contract (ICommandValidator) |
| Application | `discovery/` | MODIFY | Validate commands before registering discovered providers |
| Infrastructure | `discovery/docker_source.py` | MODIFY | Pass discovered commands through validator |
| Infrastructure | `discovery/filesystem_source.py` | MODIFY | Pass discovered commands through validator |
| Server | `config.py` | MODIFY | Add `security.allowed_commands` config section |
| Server | `bootstrap/` | MODIFY | Wire command validator into discovery pipeline |

**New Component:**

```text
domain/security/command_validator.py
    class CommandValidator:
        def __init__(self, allowed_executables: list[str], denied_patterns: list[str])
        def validate(self, command: list[str]) -> ValidationResult
        def is_safe(self, command: list[str]) -> bool
```

**Data Flow Change:**

```text
Discovery Source -> DiscoveredProvider -> [NEW: CommandValidator.validate()] -> Register/Reject
```

**Architectural Note:** The validator belongs in the domain layer because command safety is a business rule. It has no external dependencies -- just string matching against allowlists. The allowlist is configured via config and injected at bootstrap.

---

### Feature 4: Saga Persistence (P1)

**Problem:** SagaManager stores all saga state in-memory (`_active_sagas`, `_event_sagas`). Process restart loses all saga state -- active sagas vanish, retry counts reset.

**Integration Points:**

| Layer | Component | Change Type | Description |
|-------|-----------|-------------|-------------|
| Domain | `contracts/` | NEW | `saga_store.py` -- ISagaStore interface for saga state persistence |
| Infrastructure | `saga_manager.py` | MODIFY | Accept ISagaStore dependency; checkpoint after each step; restore on startup |
| Infrastructure | `persistence/` | NEW | `saga_store.py` -- SQLite + in-memory ISagaStore implementations |
| Application | `sagas/provider_recovery_saga.py` | MODIFY | Make _retry_state serializable; add idempotency guards |
| Application | `sagas/group_rebalance_saga.py` | MODIFY | Serializable state |
| Application | `sagas/provider_failover_saga.py` | MODIFY | Serializable state |
| Server | `bootstrap/__init__.py` | MODIFY | Wire ISagaStore into SagaManager |
| Server | `bootstrap/` | NEW or MODIFY | `saga_persistence.py` or add to existing init |

**New Contract (domain layer):**

```python
# domain/contracts/saga_store.py
class ISagaStore(ABC):
    @abstractmethod
    def save_checkpoint(self, saga_id: str, saga_type: str, state: dict) -> None: ...

    @abstractmethod
    def load_checkpoint(self, saga_id: str) -> dict | None: ...

    @abstractmethod
    def load_active_sagas(self) -> list[dict]: ...

    @abstractmethod
    def remove_checkpoint(self, saga_id: str) -> None: ...

    @abstractmethod
    def mark_completed(self, saga_id: str, outcome: str) -> None: ...
```

**New Infrastructure Implementation:**

```python
# infrastructure/persistence/saga_store.py
class SQLiteSagaStore(ISagaStore):
    # Stores saga checkpoints in SQLite
    # Table: saga_checkpoints (saga_id, saga_type, state_json, step_index, status, updated_at)

class InMemorySagaStore(ISagaStore):
    # For testing
```

**SagaManager Modification:**

```python
class SagaManager:
    def __init__(self, command_bus, event_bus, saga_store: ISagaStore | None = None):
        self._saga_store = saga_store or NullSagaStore()

    def start_saga(self, saga, initial_data):
        # ... existing logic ...
        # NEW: checkpoint after configure()
        self._saga_store.save_checkpoint(context.saga_id, saga.saga_type, context.to_dict())

    def _execute_saga(self, saga_id):
        # ... after each step completes ...
        # NEW: checkpoint after step completion
        self._saga_store.save_checkpoint(saga_id, saga.saga_type, context.to_dict())

    def restore_sagas(self) -> int:
        # NEW: called at startup
        checkpoints = self._saga_store.load_active_sagas()
        for cp in checkpoints:
            # Reconstruct saga from checkpoint
            # Resume execution from last completed step
```

**Idempotency for EventTriggeredSaga:**

```python
class ProviderRecoverySaga(EventTriggeredSaga):
    def handle(self, event):
        # Add idempotency: check if we already processed this event
        if self._already_processed(event.event_id):
            return []
        commands = self._handle_degraded(event)
        self._mark_processed(event.event_id)
        return commands
```

**Data Flow Change:**

```text
BEFORE: SagaManager -> execute step -> update in-memory state -> (lost on restart)
AFTER:  SagaManager -> execute step -> checkpoint to ISagaStore -> (survives restart)
        Startup: SagaManager.restore_sagas() -> load checkpoints -> resume from last step
```

---

### Feature 5: Circuit Breaker Persistence (P1)

**Problem:** CircuitBreaker state is in-memory. Process restart resets all circuit breakers to CLOSED, potentially sending traffic to known-failing providers.

**Integration Points:**

| Layer | Component | Change Type | Description |
|-------|-----------|-------------|-------------|
| Domain | `model/circuit_breaker.py` | MODIFY | Add to_dict()/from_dict() serialization; add CircuitBreakerSnapshot |
| Domain | `contracts/` | NEW or MODIFY | Add ICircuitBreakerStore interface (or piggyback on ISagaStore pattern) |
| Infrastructure | `persistence/` | NEW | `circuit_breaker_store.py` -- SQLite/file persistence for CB state |
| Server | `bootstrap/` | MODIFY | Wire CB store; restore CB state on startup |

**Key Design Decision:** Circuit breaker state is per-provider. Rather than a separate store, the most architecturally clean approach is to include CB state in the provider's event stream or snapshot:

**Option A (Recommended): Persist as part of Provider aggregate snapshot.**

The EventSourcedProvider already has ProviderSnapshot with health data. Extend it to include circuit breaker state:

```python
@dataclass
class ProviderSnapshot:
    # ... existing fields ...
    circuit_breaker_state: str = "closed"  # NEW
    circuit_breaker_failure_count: int = 0  # NEW
    circuit_breaker_opened_at: float | None = None  # NEW
```

This piggybacks on the existing snapshot infrastructure -- no new store needed.

**Option B: Separate circuit breaker event.**

Add domain events: `CircuitBreakerOpened`, `CircuitBreakerClosed`. These flow through the existing event store. On replay, circuit breaker state reconstructs.

**Recommendation:** Option A for simplicity. The circuit breaker is a child entity of the Provider aggregate, so its state naturally belongs in the Provider's snapshot.

**Data Flow Change:**

```text
BEFORE: Provider restart -> CircuitBreaker() -> CLOSED (fresh)
AFTER:  Provider restart -> load snapshot -> CircuitBreaker.from_snapshot(cb_state) -> OPEN/CLOSED (preserved)
```

---

### Feature 6: Health Check Backoff (P2)

**Problem:** BackgroundWorker in gc.py health-checks every provider at the same fixed interval regardless of state. COLD providers get checked (wasteful), DEGRADED providers get hammered.

**Integration Points:**

| Layer | Component | Change Type | Description |
|-------|-----------|-------------|-------------|
| Domain | `model/health_tracker.py` | MODIFY | Add state-aware backoff calculation; add jitter |
| Domain | `policies/` | NEW or MODIFY | `health_check_policy.py` -- interval rules per state |
| Application | (none) | -- | -- |
| Infrastructure | `gc.py` | MODIFY | BackgroundWorker skips providers based on state and backoff |

**New Policy (domain layer):**

```python
# domain/policies/health_check_policy.py
class HealthCheckPolicy:
    """Determines health check interval based on provider state."""

    def should_check(self, state: ProviderState, health: HealthTracker, now: float) -> bool:
        if state == ProviderState.COLD:
            return False  # Never check COLD providers
        if state == ProviderState.DEAD:
            return False  # Dead providers need restart, not health check
        if state == ProviderState.DEGRADED:
            return health.can_retry()  # Use existing backoff
        if state == ProviderState.READY:
            return True  # Always check READY
        return False

    def next_check_interval(self, state: ProviderState, health: HealthTracker) -> float:
        if state == ProviderState.READY:
            return self._base_interval  # e.g., 60s
        if state == ProviderState.DEGRADED:
            backoff = health._calculate_backoff()
            jitter = random.uniform(0, backoff * 0.1)
            return backoff + jitter
        return float('inf')  # Don't schedule
```

**BackgroundWorker Modification:**

```python
# gc.py
for provider_id, provider in providers_snapshot:
    if self.task == "health_check":
        # NEW: Skip based on policy
        if not self._health_policy.should_check(provider.state, provider.health, now):
            continue
        # ... existing health check logic ...
```

**Data Flow Change:**

```text
BEFORE: health_check loop -> check ALL providers every N seconds
AFTER:  health_check loop -> consult HealthCheckPolicy per provider -> skip COLD/DEAD, backoff DEGRADED
```

---

### Feature 7: Event Store Snapshots (P2)

**Problem:** EventStoreSnapshot class exists but is incomplete. No automatic triggering, no integration with aggregate rehydration beyond EventSourcedProviderRepository.

**Integration Points:**

| Layer | Component | Change Type | Description |
|-------|-----------|-------------|-------------|
| Domain | `model/event_sourced_provider.py` | MODIFY | Ensure create_snapshot() and from_snapshot() are complete and correct |
| Infrastructure | `event_store.py` | MODIFY | EventStoreSnapshot: add cleanup of old snapshots, add configurable interval |
| Infrastructure | `event_sourced_repository.py` | MODIFY | Trigger snapshot after N events automatically; integrate with load path |
| Server | `bootstrap/event_store.py` | MODIFY | Wire snapshot store with configurable path and interval |
| Server | `config.py` | MODIFY | Add `event_store.snapshot_interval` and `event_store.snapshot_path` config |

**Existing Components Already Present:**

- `EventStoreSnapshot` class in `infrastructure/event_store.py` -- has save/load/should_snapshot
- `ProviderSnapshot` dataclass in `domain/model/event_sourced_provider.py` -- has to_dict/from_dict
- `EventSourcedProviderRepository` -- already has `_create_snapshot()` and `_get_events_since_snapshot()`

**What's Missing:**

1. Snapshot cleanup (old snapshots accumulate)
2. Configurable snapshot interval via config.yaml
3. Automatic snapshot trigger is in add() but not battle-tested
4. No snapshot for circuit breaker state (see Feature 5 above)

**This feature is mostly about completing and hardening existing code, not creating new components.**

---

### Feature 8: Rate Limiter Middleware (P2)

**Problem:** Rate limiting is currently coupled to the server layer via `server/validation.py` -> `check_rate_limit()`. It only works for MCP tool calls, not HTTP API or CLI. Need transport-agnostic enforcement.

**Integration Points:**

| Layer | Component | Change Type | Description |
|-------|-----------|-------------|-------------|
| Application | `services/` or new `middleware/` | NEW | `rate_limit_middleware.py` -- transport-agnostic rate limit enforcement |
| Application | `ports/` | NEW or MODIFY | Port interface for rate limiting (already IRateLimiter in context.py) |
| Domain | `security/rate_limiter.py` | UNCHANGED | Core rate limiting logic stays in domain |
| Server | `validation.py` | MODIFY | Delegate to application-layer middleware |
| Server | `http_auth_middleware.py` | MODIFY | Add rate limiting to HTTP pipeline |
| Server | `tools/` | MODIFY | Use middleware instead of direct check_rate_limit() |

**New Application Service:**

```python
# application/services/rate_limit_middleware.py
class RateLimitMiddleware:
    """Transport-agnostic rate limiting."""

    def __init__(self, rate_limiter: RateLimiter, config: RateLimitConfig):
        self._limiter = rate_limiter
        self._config = config

    def check(self, scope: RateLimitScope, key: str) -> RateLimitResult:
        """Check rate limit for a given scope and key."""
        composite_key = f"{scope.value}:{key}"
        return self._limiter.consume(composite_key)

    def enforce(self, scope: RateLimitScope, key: str) -> None:
        """Check and raise if limit exceeded."""
        result = self.check(scope, key)
        if not result.allowed:
            raise RateLimitExceeded(limit=result.limit, ...)
```

**Data Flow Change:**

```text
BEFORE: MCP tool call -> server/validation.py -> check_rate_limit() -> domain RateLimiter
AFTER:  {MCP tool, HTTP API, CLI} -> RateLimitMiddleware.enforce() -> domain RateLimiter
```

---

### Feature 9: Typing and Code Quality (P2)

**Problem:** Legacy type hints (`Optional[str]`, `List[str]`), missing `py.typed` marker, inconsistent logging (some f-string, some structured).

**Integration Points:**

| Layer | Component | Change Type | Description |
|-------|-----------|-------------|-------------|
| ALL | Multiple files | MODIFY | Replace legacy typing imports with Python 3.11+ syntax |
| Config | `pyproject.toml` | MODIFY | Enable stricter mypy/pyright settings |
| Package | `packages/core/mcp_hangar/` | NEW | `py.typed` marker file |
| ALL | Multiple files | MODIFY | Standardize logging to structlog keyword args |

**Key Files Needing Type Fixes (from code review):**

- `infrastructure/saga_manager.py`: Uses `Optional["Command"]` (line 42-43)
- `domain/security/rate_limiter.py`: Uses `dict[str, any]` (lowercase `any` -- line 57, 292)
- `server/context.py`: Uses `Optional` from typing (line 9)
- Various files: Mix of f-string logging and structured logging

**No architectural changes.** Purely code quality.

---

### Feature 10: Testing Gaps (P2)

**Problem:** No property-based tests for state machine, no saga rollback tests, no performance benchmarks.

**Integration Points:**

| Layer | Component | Change Type | Description |
|-------|-----------|-------------|-------------|
| Tests | `tests/unit/` | NEW | Property-based tests using Hypothesis for Provider state machine |
| Tests | `tests/unit/` | NEW | Saga rollback/compensation tests |
| Tests | `tests/integration/` | NEW | Performance benchmarks for event store, tool invocation |
| Config | `pyproject.toml` | MODIFY | Add hypothesis to dev dependencies |

**No production code changes.** Test-only additions.

---

### Feature 11: Docker Discovery Resilience (P2)

**Problem:** DockerDiscoverySource in `infrastructure/discovery/docker_source.py` has no retry/reconnect on transient Docker API failures. Connection loss kills discovery permanently.

**Integration Points:**

| Layer | Component | Change Type | Description |
|-------|-----------|-------------|-------------|
| Infrastructure | `discovery/docker_source.py` | MODIFY | Add retry with exponential backoff; reconnect on socket errors |
| Domain | `events.py` | ADD EVENTS | `DiscoverySourceReconnected`, `DiscoverySourceError` (or use existing `DiscoverySourceHealthChanged`) |
| Application | `discovery/` | MODIFY | Track source health state in orchestrator |

**Retry Strategy:**

```python
class DockerDiscoverySource(DiscoverySource):
    def __init__(self, ...):
        self._max_retries = 3
        self._backoff_base = 2.0
        self._client: docker.DockerClient | None = None

    def discover(self) -> list[DiscoveredProvider]:
        for attempt in range(self._max_retries + 1):
            try:
                client = self._get_or_reconnect_client()
                return self._scan_containers(client)
            except (DockerException, ConnectionError) as e:
                if attempt == self._max_retries:
                    raise
                backoff = self._backoff_base ** attempt
                time.sleep(backoff)
                self._client = None  # Force reconnect

    def _get_or_reconnect_client(self) -> docker.DockerClient:
        if self._client is None:
            self._client = docker.DockerClient(base_url=self._socket_url)
        return self._client
```

**Data Flow Change:**

```text
BEFORE: discover() -> Docker API call -> failure -> raise -> discovery stops
AFTER:  discover() -> Docker API call -> failure -> retry with backoff -> reconnect -> retry -> (emit DiscoverySourceHealthChanged)
```

---

## Component Dependency Map

### New Components

| Component | Layer | File | Depends On |
|-----------|-------|------|------------|
| CommandValidator | Domain | `domain/security/command_validator.py` | None (pure domain logic) |
| ISagaStore | Domain | `domain/contracts/saga_store.py` | None (interface) |
| SQLiteSagaStore | Infrastructure | `infrastructure/persistence/saga_store.py` | ISagaStore, sqlite3 |
| InMemorySagaStore | Infrastructure | `infrastructure/persistence/saga_store.py` | ISagaStore |
| HealthCheckPolicy | Domain | `domain/policies/health_check_policy.py` | ProviderState, HealthTracker |
| RateLimitMiddleware | Application | `application/services/rate_limit_middleware.py` | RateLimiter (domain) |

### Modified Components (by priority)

| Component | File | Feature | Priority |
|-----------|------|---------|----------|
| StdioClient | `stdio_client.py` | Concurrency safety | P0 |
| Provider | `domain/model/provider.py` | Concurrency safety, exception hygiene | P0 |
| Multiple files | Various | Exception hygiene (BLE001) | P0 |
| DiscoveredProvider pipeline | `application/discovery/` | Command injection prevention | P0 |
| SagaManager | `infrastructure/saga_manager.py` | Saga persistence | P1 |
| ProviderRecoverySaga | `application/sagas/provider_recovery_saga.py` | Saga persistence | P1 |
| CircuitBreaker | `domain/model/circuit_breaker.py` | CB persistence | P1 |
| ProviderSnapshot | `domain/model/event_sourced_provider.py` | CB persistence, snapshots | P1/P2 |
| HealthTracker | `domain/model/health_tracker.py` | Health check backoff | P2 |
| BackgroundWorker | `gc.py` | Health check backoff | P2 |
| EventStoreSnapshot | `infrastructure/event_store.py` | Event store snapshots | P2 |
| EventSourcedProviderRepository | `infrastructure/event_sourced_repository.py` | Event store snapshots | P2 |
| DockerDiscoverySource | `infrastructure/discovery/docker_source.py` | Docker resilience | P2 |
| check_rate_limit | `server/validation.py` | Rate limiter middleware | P2 |

### Unchanged Components

| Component | Reason |
|-----------|--------|
| AggregateRoot | Base class already correct |
| EventBus | Already properly structured with lock hierarchy |
| CommandBus | Pass-through dispatcher, no changes needed |
| QueryBus | Pass-through dispatcher, no changes needed |
| LockHierarchy | Already enforces correct ordering |
| DomainEvents | May add 1-2 new events but structure unchanged |
| ProviderGroup | Not affected by hardening features |

---

## Suggested Build Order

Build order is driven by: (1) priority (P0 before P1 before P2), (2) dependencies between features, (3) risk isolation.

### Phase 1: Safety Foundation (P0 -- Week 1)

**Features:** Concurrency safety, Exception hygiene, Command injection prevention

**Rationale:** These are P0 -- they prevent data loss, deadlocks, and security breaches. They have zero dependencies on each other and can be built in parallel. They also create the safety foundation that later features build on (saga persistence assumes correct locking).

**Build Order Within Phase:**

1. **Exception hygiene** -- enable ruff BLE001, sweep all files. Lowest risk, builds confidence.
2. **StdioClient race fix** -- small, focused, high-impact. Register pending before write.
3. **Provider lock audit** -- review all lock-holding paths, extract I/O from locks.
4. **Command validator** -- new domain component + discovery pipeline integration.

**Dependencies:** None between these items. Exception hygiene is a prerequisite for trusting error handling in saga persistence.

### Phase 2: State Survival (P1 -- Week 2)

**Features:** Saga persistence, Circuit breaker persistence

**Rationale:** These ensure state survives restarts. Saga persistence is the more complex feature; circuit breaker persistence is simpler and can piggyback on existing snapshot infrastructure.

**Build Order Within Phase:**

1. **ISagaStore contract** -- define interface first (domain layer).
2. **SQLiteSagaStore** -- implement persistence (infrastructure layer).
3. **SagaManager checkpoint/restore** -- modify SagaManager to use ISagaStore.
4. **Saga idempotency** -- add idempotency guards to EventTriggeredSaga implementations.
5. **Circuit breaker serialization** -- add to_dict/from_dict to CircuitBreaker.
6. **Circuit breaker in ProviderSnapshot** -- extend existing snapshot with CB state.
7. **Bootstrap wiring** -- wire ISagaStore and CB restoration into startup.

**Dependencies:** CB persistence depends on ProviderSnapshot being complete (Feature 7 overlap). Saga persistence requires exception hygiene to be done (Phase 1) so error handling is trustworthy.

### Phase 3: Operational Hardening (P2 -- Weeks 3-4)

**Features:** Health check backoff, Event store snapshots, Rate limiter middleware, Typing/code quality, Docker discovery resilience, Testing gaps

**Rationale:** P2 features improve operational quality but don't prevent data loss or security breaches. They can be built in any order.

**Build Order Within Phase (suggested):**

1. **Health check backoff** -- small, self-contained, high operational value.
2. **Event store snapshots** -- mostly completing existing code, ensures fast startup.
3. **Rate limiter middleware** -- refactoring existing code to application layer.
4. **Docker discovery resilience** -- isolated change in one file.
5. **Typing/code quality** -- sweep after all other changes to avoid merge conflicts.
6. **Testing gaps** -- always last, tests the features built above.

**Dependencies:** Typing sweep should happen after all other code changes. Tests depend on features being implemented.

---

## Cross-Cutting Concerns

### Lock Hierarchy Impact

No new lock levels needed. The existing hierarchy covers all new components:

- CommandValidator: No locks (stateless, pure function)
- ISagaStore: If SQLite, internal lock at EVENT_STORE level (30) is appropriate
- HealthCheckPolicy: No locks (stateless policy)
- RateLimitMiddleware: Delegates to domain RateLimiter which has its own locks

### Event Store Impact

- **New events needed:** Potentially `SagaCheckpointed`, `SagaRestored`, `CircuitBreakerStateChanged`
- **Event versioning:** Use `schema_version` field pattern from existing events
- **Snapshot format:** Extend ProviderSnapshot dataclass (backward compatible with defaults)

### Bootstrap Wiring Impact

The `server/bootstrap/__init__.py` bootstrap() function will gain:

- ISagaStore initialization (after event store init, step 3.5)
- SagaManager.restore_sagas() call (after saga init, step 7.5)
- HealthCheckPolicy injection into BackgroundWorker (step 11)
- RateLimitMiddleware creation (after runtime init)
- CommandValidator injection into discovery pipeline (step 13)

### Configuration Impact

New config sections (all with sensible defaults for backward compat):

```yaml
# config.yaml additions
security:
  allowed_commands:
    - python
    - node
    - npx
    - uvx
    - docker
  denied_patterns:
    - "rm -rf"
    - "curl | sh"

saga:
  persistence:
    enabled: true
    driver: sqlite  # or: memory
    path: data/sagas.db

event_store:
  snapshot_interval: 100  # events between snapshots
  snapshot_path: data/snapshots/

health_check:
  backoff:
    enabled: true
    skip_cold: true
    skip_dead: true
    max_interval_s: 300
```

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Persisting Saga State in Event Store

**What:** Using the existing event store to persist saga checkpoints as domain events.
**Why bad:** Saga checkpoints are operational state, not domain events. They would pollute event streams, interfere with aggregate replay, and create confusing versioning. Sagas are infrastructure-level orchestration, not domain behavior.
**Instead:** Use a dedicated ISagaStore with its own SQLite table. Clean separation of concerns.

### Anti-Pattern 2: Circuit Breaker as Separate Aggregate

**What:** Making CircuitBreaker an independent aggregate with its own event stream.
**Why bad:** CircuitBreaker is a child entity of Provider. It has no independent identity -- it exists only in the context of a provider. Separate streams would mean separate versioning, separate snapshots, and cross-aggregate consistency challenges.
**Instead:** Include CB state in Provider's ProviderSnapshot. CB state changes can optionally emit Provider-level events.

### Anti-Pattern 3: Rate Limiter in Domain Layer Touching Infrastructure

**What:** Adding HTTP-aware rate limiting logic (headers, IP extraction) to domain/security/rate_limiter.py.
**Why bad:** Domain layer must have NO external dependencies and no knowledge of transport. HTTP headers are a transport concern.
**Instead:** Domain provides RateLimiter (token bucket). Application provides RateLimitMiddleware (transport-agnostic). Server layer extracts HTTP-specific details and passes scope+key to middleware.

### Anti-Pattern 4: Command Validation at Process Launch Time Only

**What:** Only validating commands in `provider.py._start()` when the subprocess is about to launch.
**Why bad:** By the time _start() runs, the provider is already registered in the system. A rejected command would leave a registered-but-unlaunchable provider. Also,_start() runs under lock.
**Instead:** Validate commands at discovery/registration time. Reject before the provider enters the system.

### Anti-Pattern 5: Global Mutable Singletons for New Components

**What:** Adding `_global_saga_store: SQLiteSagaStore | None = None` with get/set functions for each new component.
**Why bad:** The codebase already has too many singletons (get_event_store, get_saga_manager, get_command_bus, etc.). Each adds testing friction and hidden coupling.
**Instead:** Wire all new components through bootstrap/ApplicationContext. Use constructor injection. The existing singletons exist for backward compatibility; new code should not create more.

---

## Scalability Considerations

| Concern | At 10 providers | At 100 providers | At 1000 providers |
|---------|-----------------|-------------------|--------------------|
| Saga checkpoints | Negligible: ~10 rows | Low: ~100 rows, single table | Moderate: index saga_type for queries |
| CB persistence | Via snapshots, no extra cost | Via snapshots, no extra cost | Via snapshots, no extra cost |
| Health check backoff | Saves ~50% of checks (COLD skipped) | Saves ~70% (many COLD) | Critical: prevents health check storm |
| Event snapshots | Rarely triggered | ~1 snapshot/provider/hour | Must snapshot: 1000 streams without snapshot = slow startup |
| Rate limiting | Single bucket | ~100 buckets + cleanup | Cleanup interval matters; consider per-provider TTL |
| Command validation | ~1 validation/startup | ~10 validations/startup | Cache validation results by command hash |

## Testing Strategy

| Feature | Unit Test | Integration Test | Property Test |
|---------|-----------|-----------------|---------------|
| Concurrency safety | Lock order assertions | Concurrent invoke_tool calls | Hypothesis: random interleaving |
| Exception hygiene | ruff BLE001 enforcement | -- | -- |
| Command injection | Allowlist matching | Discovery pipeline rejects bad commands | Hypothesis: fuzz command strings |
| Saga persistence | Checkpoint save/load | Saga survives simulated restart | Hypothesis: random saga step failure |
| CB persistence | Serialize/deserialize | CB state survives Provider reload | -- |
| Health check backoff | Policy returns correct intervals | BackgroundWorker skips correctly | Hypothesis: random state sequences |
| Event snapshots | Snapshot create/load | Repository uses snapshot on load | -- |
| Rate limiter middleware | Scope key construction | Multi-transport enforcement | -- |
| Docker resilience | Retry backoff timing | Reconnect after mock socket failure | -- |

## Sources

- Existing codebase analysis (all files listed above read in full) -- HIGH confidence
- DDD/CQRS/Event Sourcing patterns (Vaughn Vernon, Greg Young) -- HIGH confidence (well-established patterns)
- Python threading documentation (Python 3.11+) -- HIGH confidence
- SQLite as embedded persistence for saga/snapshot -- HIGH confidence (already used in event store)
