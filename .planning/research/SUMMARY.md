# Project Research Summary

**Project:** MCP Hangar v1.0 Production Hardening
**Domain:** DDD/CQRS/Event Sourcing platform hardening (concurrency, persistence, security, resilience)
**Researched:** 2026-03-08
**Confidence:** HIGH

## Executive Summary

MCP Hangar is a production-grade MCP provider platform built on DDD, CQRS, and Event Sourcing with thread-based concurrency. The v1.0 hardening milestone covers 11 features across P0/P1/P2 priorities that address real production gaps: lock hierarchy violations that can deadlock, bare exception catches that silently swallow failures, unvalidated commands from discovery sources, ephemeral saga and circuit breaker state lost on restart, unbounded event replay on startup, and missing property-based testing for the core state machine. The critical finding is that the existing codebase already contains most of the primitives needed -- `InputValidator`, `BackoffStrategy`, `TokenBucket`, `SagaContext.to_dict()`, `ProviderSnapshot`, `SQLiteConnectionFactory` -- but they are either unwired, incomplete, or bypassed in key code paths.

The recommended approach is zero new third-party dependencies. All 11 features can be implemented using Python stdlib (`sqlite3`, `threading`, `json`) and existing project infrastructure (`TrackedLock`, `MigrationRunner`, `EventSerializer`). This is the correct outcome for a stability milestone -- adding dependencies during hardening is counterproductive. The three SQLite tables needed (saga checkpoints, circuit breaker state, snapshots) colocate in the existing event store database using the existing `MigrationRunner` for schema management.

The top risks are: (1) I/O-under-lock removal in `Provider._start()` and `_refresh_tools()` introduces TOCTOU races that require structural restructuring, not simple extraction; (2) the 42 bare `except Exception:` catches must be categorized into fault-barriers, cleanup, and bug-hiding before BLE001 enforcement -- uniform treatment causes background worker crash loops; (3) saga persistence requires idempotency design before implementation because event-triggered sagas have non-idempotent side effects that duplicate on restart replay; (4) a live lock hierarchy violation exists in `ProviderGroup._try_start_member()` (level 11 acquiring level 10) that creates latent deadlock risk.

## Key Findings

### Recommended Stack

No new third-party dependencies. All hardening features use existing stack: Python 3.11+ stdlib, SQLite (already a dependency via event store), `hypothesis>=6.90.0` (already in dev deps), `ruff>=0.3.0` (already in dev deps), `mypy>=1.8.0` (already in dev deps).

**Core technologies (all existing):**

- **sqlite3 (stdlib):** Saga checkpoints, circuit breaker state, snapshot store -- reuses existing `SQLiteConnectionFactory` and `MigrationRunner`
- **threading (stdlib):** `TrackedLock` for new stores at `LockLevel.REPOSITORY` (31), `threading.Event` for `_start()` concurrency fix
- **hypothesis (dev dep):** `RuleBasedStateMachine` for Provider state machine property testing -- already used in `tests/unit/observability/test_property_based.py`
- **ruff BLE001 rule:** Bare-except enforcement -- must enable AFTER categorization audit of 42 instances

**Configuration changes needed:**

- `pyproject.toml`: Add `BLE` to ruff `select` list, remove `B904` from ignore list
- `pyproject.toml`: Incrementally enable mypy `check_untyped_defs`, `no_implicit_optional`, `disallow_incomplete_defs`
- 3 new SQLite tables via `MigrationRunner`: `saga_checkpoints`, `circuit_breaker_state`, `snapshots`

### Expected Features

**Must have (table stakes):**

- Saga persistence with checkpoint/resume -- in-memory state lost on restart leaves providers in inconsistent states
- Circuit breaker state persistence -- resets on restart cause cascading failures against known-bad providers
- Event store snapshots -- replay grows unbounded, startup time degrades linearly with history
- Exponential backoff with jitter for health checks -- fixed-interval checks hammer degraded providers
- Command validation for discovery sources -- Docker labels execute arbitrary commands with zero validation
- Transport-agnostic rate limiting -- only MCP tool calls are rate-limited; HTTP API, CLI, programmatic callers bypass
- Property-based state machine testing -- example-based tests cannot verify ALL transition sequences maintain invariants

**Should have (differentiators -- defer to follow-up):**

- Saga compensation on partial failure (requires saga persistence first)
- HALF_OPEN circuit breaker state (small code change, well-understood pattern)
- Snapshot compaction / old event pruning (requires snapshots first)
- Rate limit metrics and Prometheus observability

**Defer (anti-features):**

- Distributed saga coordination, external circuit breaker service, real-time event streaming, ML anomaly detection, async/asyncio rewrite, custom binary snapshot format

### Architecture Approach

The 11 features integrate into the existing 4-layer DDD architecture (domain -> application -> infrastructure -> server) without violating layer boundaries. Six new components are needed: `CommandValidator` (domain), `ISagaStore` interface (domain/contracts), `SQLiteSagaStore` (infrastructure), `InMemorySagaStore` (infrastructure), `HealthCheckPolicy` (domain/policies), and `RateLimitMiddleware` (application). The circuit breaker persistence piggybacks on the existing `ProviderSnapshot` dataclass rather than creating a separate store -- CB is a child entity of Provider, not an independent aggregate.

**Major components (new):**

1. **CommandValidator** (domain/security) -- Allowlist-based validation for discovery-sourced commands, applied at registration time, not launch time
2. **ISagaStore + SQLiteSagaStore** (domain/contracts + infrastructure/persistence) -- Checkpoint after each saga step, restore incomplete sagas on bootstrap
3. **HealthCheckPolicy** (domain/policies) -- State-aware health check scheduling: skip COLD/DEAD, backoff DEGRADED, normal interval for READY
4. **RateLimitMiddleware** (application/services) -- Wraps command/query bus execution for transport-agnostic enforcement

**Major components (modified):**

1. **Provider._start() / _refresh_tools()** -- Restructure to extract I/O from lock scope using state-guard + `threading.Event` pattern
2. **StdioClient** -- Fix request-response race: register PendingRequest BEFORE writing to stdin
3. **ProviderSnapshot** -- Extend with circuit breaker state fields; fix value object deserialization in `from_snapshot()`

### Critical Pitfalls

1. **I/O-under-lock removal introduces TOCTOU races** -- `_refresh_tools()` is called inside locked `invoke_tool()`. Moving I/O outside requires restructuring to separate lock-acquire/release cycles, not simple extraction. `_start()` blocks ALL threads for 30+ seconds during cold start. Fix: use `INITIALIZING` state guard + `threading.Event` for waiters.

2. **42 bare excepts must be categorized before BLE001** -- Three categories: fault-barriers (background loops that MUST catch broadly), cleanup paths (narrow to specific exceptions), and bug-hiding (replace entirely). Uniform treatment crashes `BackgroundWorker._loop()` on first unhandled exception, permanently killing GC or health check workers.

3. **Saga persistence breaks idempotency** -- `ProviderRecoverySaga.handle()` has side effects (incrementing counters, issuing commands) that are not idempotent. On restart replay, degraded providers get extra restart attempts, potentially exceeding `max_retries` immediately. Fix: track `last_event_id` per saga, set `_replaying` flag during replay to suppress command emission.

4. **ProviderGroup._try_start_member() violates lock hierarchy** -- Acquires Provider lock (level 10) while holding ProviderGroup lock (level 11). This creates deadlock risk when combined with event handlers calling `group.report_success()` while holding Provider lock. Fix: release group lock before `ensure_ready()`, re-acquire after.

5. **Snapshot version mismatch causes silent data corruption** -- Snapshot save and event append are not in the same transaction. Concurrent operations cause version drift. Fix: save snapshots inside `EventStore.append()` lock scope, add version consistency check in `from_snapshot()`.

## Implications for Roadmap

Based on research, the 11 features group into 3 phases driven by priority, dependency ordering, and risk isolation.

### Phase 1: Safety Foundation (P0)

**Rationale:** P0 features prevent deadlocks, data loss, and security breaches. They have zero dependencies on each other. Exception hygiene must complete before saga persistence (Phase 2) because saga error handling depends on trustworthy exception propagation. Concurrency fixes must precede persistence work because persistence introduces new lock-holding paths.

**Delivers:** A codebase where locks are held correctly, exceptions are handled specifically, and discovery-sourced commands are validated against an allowlist. The foundation that all subsequent hardening features build on.

**Addresses features:**

- Concurrency safety (I/O-under-lock in `_start()`, `_refresh_tools()`, `ProviderGroup._try_start_member()`)
- Exception hygiene (42 bare excepts categorized and fixed, BLE001 enabled)
- Command injection prevention (`CommandValidator` wired into discovery pipeline)

**Avoids pitfalls:**

- Pitfall 1 (TOCTOU races) -- restructure `_refresh_tools()` to separate lock cycles
- Pitfall 2 (`_start()` blocking) -- use `INITIALIZING` state guard + `threading.Event`
- Pitfall 3 (crash loops from blind except removal) -- categorize before enabling BLE001
- Pitfall 4 (command injection) -- validate at discovery/registration time, not launch time
- Pitfall 10 (lock hierarchy violation) -- release group lock before `ensure_ready()`

**Build order within phase:**

1. Exception hygiene audit (categorize 42 catches, lowest implementation risk)
2. StdioClient race fix (register pending before write, small and focused)
3. Provider lock restructuring (`_start()`, `_refresh_tools()`, `ProviderGroup._try_start_member()`)
4. Command validator (new domain component + discovery pipeline wiring)

### Phase 2: State Survival (P1)

**Rationale:** These ensure state survives restarts. Saga persistence is the highest-complexity feature in the milestone and benefits from maximum iteration time. Circuit breaker persistence is simpler and piggybacks on the existing `ProviderSnapshot` infrastructure. Both share SQLite patterns (same `MigrationRunner`, same connection factory).

**Delivers:** Sagas checkpoint after each step and resume on restart. Circuit breaker state persists in provider snapshots. No more silent state loss on process restart.

**Addresses features:**

- Saga persistence with checkpoint/resume
- Circuit breaker state persistence

**Avoids pitfalls:**

- Pitfall 6 (saga idempotency) -- design idempotency mechanism BEFORE implementing persistence; track `last_event_id`, use `_replaying` flag
- Pitfall 7 (stale circuit breaker state) -- persist failure count only, not timing; health-check on restore before honoring persisted OPEN state

**Build order within phase:**

1. `ISagaStore` contract (domain layer interface)
2. `SQLiteSagaStore` implementation + schema migration
3. `SagaManager` checkpoint/restore logic
4. Saga idempotency guards on `EventTriggeredSaga` implementations
5. Circuit breaker in `ProviderSnapshot` (extend existing dataclass)
6. Bootstrap wiring for both

**Depends on Phase 1:** Exception hygiene must be complete so saga error handling is trustworthy. Lock restructuring must be complete so saga persistence does not introduce new I/O-under-lock.

### Phase 3: Operational Hardening (P2)

**Rationale:** P2 features improve operational quality without preventing data loss or security breaches. They can be built in any order. Testing and typing sweeps should come last to avoid merge conflicts and to exercise hardened code paths.

**Delivers:** Fast startup via snapshots, intelligent health check scheduling, transport-agnostic rate limiting, resilient Docker discovery, improved type safety, and property-based testing of the core state machine.

**Addresses features:**

- Health check backoff with jitter
- Event store snapshots for fast aggregate replay
- Rate limiter middleware (transport-agnostic)
- Typing and code quality (`py.typed`, mypy strictness)
- Testing gaps (property-based state machine tests)
- Docker discovery resilience (retry/reconnect)

**Avoids pitfalls:**

- Pitfall 5 (snapshot version mismatch) -- save snapshots inside `EventStore.append()` lock scope
- Pitfall 8 (backoff-on-backoff) -- document that health backoff + saga backoff creates 120s max combined delay; share time source
- Pitfall 9 (rate limiter migration) -- atomic changeset: add middleware + remove old `check_rate_limit()` in same change
- Pitfall 11 (Docker discovery duplicates) -- track containers by ID + fingerprint across reconnections
- Pitfall 12 (snapshot value objects) -- use value object constructors in `from_snapshot()`
- Pitfall 14 (`py.typed` exposure) -- fix all mypy errors BEFORE adding marker

**Build order within phase:**

1. Health check backoff (self-contained, high operational value)
2. Event store snapshots (mostly completing existing code)
3. Rate limiter middleware (refactoring existing code to application layer)
4. Docker discovery resilience (isolated single-file change)
5. Typing and code quality (sweep after all other code changes)
6. Property-based testing (last -- tests the hardened code)

### Phase Ordering Rationale

- **P0 first** because concurrency and security fixes are prerequisites for everything else. Saga persistence under broken locks or with swallowed exceptions is worse than no persistence.
- **P1 second** because state survival is the highest-value production improvement. Ephemeral saga and circuit breaker state is the biggest operational risk after P0 safety.
- **P2 third** because these are improvements, not fixes. They make the system better but don't prevent data loss or security breaches.
- **Within each phase**, build order follows dependency chains: contracts before implementations, audits before enforcement, infrastructure before wiring.
- **Testing and typing last** because they catch regressions in hardened code and avoid merge conflicts during active development.

### Research Flags

Phases likely needing deeper research during planning:

- **Phase 1, Concurrency safety:** The `_start()` restructuring is the highest-risk change in the milestone. The interaction between `threading.Event`, `INITIALIZING` state guard, and the existing `ensure_ready()` callers needs detailed design. The `_refresh_tools()` restructuring to separate lock cycles is non-trivial because it changes the `invoke_tool()` control flow.
- **Phase 2, Saga persistence:** Idempotency design for `EventTriggeredSaga` vs step-based `Saga` requires two different persistence models. Step-based sagas need checkpoint-resume; event-triggered sagas need event-position-tracking. The interaction between `SagaManager._handle_event()` and the new persistence layer needs careful sequencing design.
- **Phase 3, Event store snapshots:** The snapshot version coordination gap (snapshot save not transactional with event append) needs a concrete design. The existing `EventStoreSnapshot` class and `ProviderSnapshot` dataclass partially overlap -- need to clarify which abstraction owns what.

Phases with standard patterns (skip detailed research):

- **Phase 1, Exception hygiene:** Well-defined audit + categorize + fix pattern. No design decisions needed.
- **Phase 1, Command injection prevention:** Wire existing `InputValidator` into discovery pipeline. Straightforward.
- **Phase 2, Circuit breaker persistence:** Extend `ProviderSnapshot` with 3 fields. Simple, low-risk.
- **Phase 3, Health check backoff:** Add jitter to existing formula, make `BackgroundWorker` state-aware. Well-understood patterns.
- **Phase 3, Rate limiter middleware:** Move enforcement from server to application layer. Standard middleware pattern.
- **Phase 3, Docker discovery resilience:** Add retry with backoff to single file. Standard retry pattern.
- **Phase 3, Typing/code quality:** Mechanical modernization sweep.
- **Phase 3, Property-based testing:** Hypothesis `RuleBasedStateMachine` is well-documented and already used in the codebase.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All findings from direct codebase analysis of `pyproject.toml`, existing infrastructure modules. Zero new dependencies is verified. |
| Features | HIGH | All 11 features traced through existing code. Gaps identified at specific file/line level. Complexity assessments based on actual code state. |
| Architecture | HIGH | All integration points verified against existing layer structure. Lock hierarchy, event flow, and bootstrap wiring confirmed. |
| Pitfalls | HIGH | All 16 pitfalls identified from actual code patterns, not theoretical concerns. Lock hierarchy violation verified in `ProviderGroup`. Bare except count (42) from grep. |

**Overall confidence:** HIGH

All research based on direct codebase analysis of 20+ source files across all architectural layers. No reliance on external documentation for implementation specifics. All existing code paths traced.

### Gaps to Address

- **Snapshot version coordination mechanism:** The exact design for transactional snapshot-save-with-event-append needs work during Phase 3 planning. Three options identified (compare-and-snapshot, save-inside-append-lock, post-replay verification) but no recommendation locked in.
- **`EventTriggeredSaga` vs `Saga` persistence models:** These are fundamentally different (event-position-tracking vs checkpoint-resume) but will share the same `ISagaStore` interface. The interface design needs to accommodate both without leaking implementation details.
- **Health check backoff + saga backoff interaction:** The combined worst-case is 120s recovery time. Whether this is acceptable or whether a shared time source is needed requires operator input or a documented SLA decision.
- **`ProviderGroup._try_start_member()` fix scope:** The lock hierarchy violation fix (release group lock before `ensure_ready()`) may reveal other call paths with the same pattern (`start_all()` has the same issue). A full audit of `ProviderGroup` lock usage is needed during Phase 1 planning.
- **Rate limiter key scheme migration:** Keeping identical keys during middleware migration is recommended, but the exact `key_extractor: Callable` design needs specification during Phase 3 planning.

## Sources

### Primary (HIGH confidence)

- **Direct codebase analysis:** `packages/core/mcp_hangar/` -- 20+ source files across domain, application, infrastructure, and server layers
- **`CLAUDE.md`:** Architecture constraints, lock hierarchy rules, forbidden patterns, layer dependencies
- **`.planning/PROJECT.md`:** Milestone context, feature priorities, design constraints
- **`pyproject.toml`:** Current dependencies, ruff config, mypy config, existing dev dependencies
- **`infrastructure/persistence/database_common.py`:** `SQLiteConnectionFactory`, `MigrationRunner`, `SQLiteConfig`
- **`infrastructure/lock_hierarchy.py`:** `TrackedLock`, `LockLevel` enum, lock ordering rules
- **`domain/model/provider.py`:** Provider aggregate, state machine, `VALID_TRANSITIONS`, lock-holding paths
- **`infrastructure/saga_manager.py`:** `SagaManager`, `SagaContext.to_dict()`, in-memory-only storage
- **`domain/model/circuit_breaker.py`:** `CircuitBreaker`, `to_dict()`, `threading.Lock` (not TrackedLock)
- **`domain/security/input_validator.py`:** `InputValidator`, allowlist/blocklist, `validate_command()`
- **`domain/security/rate_limiter.py`:** `InMemoryRateLimiter`, `TokenBucket`, global singleton anti-pattern
- **Python 3.11 stdlib documentation:** `sqlite3`, `threading`, `json`, `dataclasses` capabilities

### Secondary (MEDIUM confidence)

- **DDD/CQRS/Event Sourcing patterns** (Vaughn Vernon, Greg Young) -- established patterns applied to architecture decisions
- **Hypothesis documentation** -- `RuleBasedStateMachine` API and testing patterns

---

*Research completed: 2026-03-08*
*Ready for roadmap: yes*
