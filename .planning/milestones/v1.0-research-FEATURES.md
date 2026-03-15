# Feature Landscape

**Domain:** Production hardening for MCP provider platform (MCP Hangar)
**Researched:** 2026-03-08
**Confidence:** HIGH (based on direct codebase analysis of 20+ source files across all layers)

---

## Table Stakes

Features that production deployments require. Missing = system is unreliable under real-world conditions (restarts, failures, growth).

### 1. Saga Persistence and Checkpointing

| Attribute | Detail |
|-----------|--------|
| **Why Expected** | In-memory saga state is lost on restart. Long-running recoveries and failovers silently vanish, leaving providers in inconsistent states with no trace of what happened. |
| **Complexity** | High |
| **Existing Code** | 3 sagas: `ProviderRecoverySaga`, `GroupRebalanceSaga`, `ProviderFailoverSaga` -- all `EventTriggeredSaga` subclasses. `SagaManager` handles dispatch. `SagaContext` has `to_dict()` serialization. `SagaState` enum covers full lifecycle (NOT_STARTED, RUNNING, COMPLETED, COMPENSATING, COMPENSATED, FAILED). |
| **Gap** | All state lives in Python dicts (`_retry_state`, `_active_failovers`, `_member_to_group`). No persistence backend. No checkpoint triggers. No resume-on-bootstrap logic. Recovery saga comment explicitly says "In a real implementation, you would use a scheduler." |
| **What's Needed** | Persistence backend (SQLite table for saga state), checkpoint triggers on saga state transitions, bootstrap-time saga resume, idempotent step execution for crash recovery. |

### 2. Circuit Breaker State Persistence

| Attribute | Detail |
|-----------|--------|
| **Why Expected** | Circuit breaker state resets on restart. A provider that was tripping the breaker gets retried immediately after restart, causing cascading failures against a known-bad provider. |
| **Complexity** | Medium |
| **Existing Code** | `CircuitBreaker` in `domain/model/circuit_breaker.py` with CLOSED/OPEN states. `to_dict()` serialization exists. Thread-safe with `threading.Lock()`. `CircuitBreakerConfig` has `failure_threshold` and `reset_timeout_s`. |
| **Gap** | No persistence hook. No restore-on-bootstrap. State is simple (state + failure_count + opened_at) but completely ephemeral. Missing HALF_OPEN state is a separate concern (listed as differentiator). |
| **What's Needed** | Save hook on state changes, restore from storage on bootstrap, storage backend choice (SQLite or event store). |

### 3. Event Store Snapshots

| Attribute | Detail |
|-----------|--------|
| **Why Expected** | Without snapshots, event replay grows unbounded. Startup time degrades linearly with event history. For long-running production deployments with thousands of state transitions, this becomes a real problem. |
| **Complexity** | High |
| **Existing Code** | Domain-side plumbing exists: `ProviderSnapshot` dataclass with `to_dict()`/`from_dict()`, `EventSourcedProviderRepository` has `snapshot_store`, `snapshot_interval`, `_create_snapshot` method. `EventStoreSnapshot` class exists in `infrastructure/event_store.py` but only for `FileEventStore`. |
| **Gap** | `IEventStore` contract lacks snapshot methods. `SQLiteEventStore` has no snapshots table. The two event store abstractions (`domain/contracts/event_store.py` IEventStore vs `infrastructure/event_store.py` EventStore ABC) are not aligned on snapshot support. |
| **What's Needed** | Add snapshot methods to `IEventStore` contract, create snapshots table in SQLiteEventStore, integrate snapshot-aware replay in `EventSourcedProviderRepository`, snapshot creation trigger (every N events). |

### 4. Exponential Backoff with Jitter for Health Checks

| Attribute | Detail |
|-----------|--------|
| **Why Expected** | Fixed-interval health checks hammer degraded providers. Without jitter, multiple providers failing simultaneously create thundering herd on recovery. State-unaware scheduling wastes resources checking healthy providers at the same rate as degraded ones. |
| **Complexity** | Medium |
| **Existing Code** | `HealthTracker` in `domain/model/health_tracker.py` has `_calculate_backoff()` computing `min(60, 2^consecutive_failures)`. `can_retry()` and `time_until_retry()` exist. `BackgroundWorker` in `gc.py` uses fixed `interval_s` for ALL providers regardless of state. |
| **Gap** | No jitter in backoff formula. `BackgroundWorker` runs health checks at uniform interval for all providers -- healthy, degraded, and dead get same frequency. No state-aware scheduling. |
| **What's Needed** | Add jitter to `_calculate_backoff()` (e.g., `backoff * (0.5 + random(0, 0.5))`), make `BackgroundWorker` state-aware (healthy=normal interval, degraded=backoff+jitter, dead=longer backoff with ceiling). |

### 5. Command Validation for Discovery-Sourced Providers

| Attribute | Detail |
|-----------|--------|
| **Why Expected** | Docker/K8s discovery sources accept arbitrary commands from labels/annotations without validation. A malicious or misconfigured label can inject arbitrary commands into provider startup. This is a security boundary issue. |
| **Complexity** | Medium |
| **Existing Code** | `InputValidator` in `domain/security/input_validator.py` has `validate_command()` with allowlist/blocklist and `DANGEROUS_PATTERNS` regex list. Docker discovery (`docker_source.py` line 206) does raw `cmd.split()` with NO validation. `allowed_commands` parameter exists but defaults to `None` (blocklist only). |
| **Gap** | Discovery pipeline bypasses `InputValidator` entirely. The validator exists and works -- it's just not wired into the discovery path. |
| **What's Needed** | Wire `InputValidator.validate_command()` into discovery pipeline before provider registration. Apply validation in `DockerDiscoverySource` and `KubernetesDiscoverySource`. Consider making allowlist mandatory for discovery-sourced providers (higher trust bar than manually configured ones). |

### 6. Transport-Agnostic Rate Limiting

| Attribute | Detail |
|-----------|--------|
| **Why Expected** | Rate limiting exists only at server layer (`server/validation.py`). Direct command bus usage, internal callers, programmatic API users, and alternative transports bypass rate limiting entirely. A single noisy client can monopolize provider resources. |
| **Complexity** | Medium |
| **Existing Code** | Full implementation exists: `TokenBucket` + `InMemoryRateLimiter` + `CompositeRateLimiter` in `domain/security/rate_limiter.py`. `RateLimitResult` has `to_headers()` for HTTP responses. Currently enforced only in `server/validation.py` via `check_rate_limit()`. Auth middleware has its own separate `AuthRateLimiter`. |
| **Gap** | Enforcement point is too high in the stack. Domain-layer implementation, server-layer enforcement. Any entry point that doesn't go through `server/validation.py` skips rate limiting. |
| **What's Needed** | Move enforcement to command bus middleware or application service decorator. All tool invocation paths must pass through rate limiting regardless of transport (stdio, HTTP, programmatic). Keep server-layer response formatting (headers) but move the check inward. |

### 7. Property-Based State Machine Testing

| Attribute | Detail |
|-----------|--------|
| **Why Expected** | The provider state machine is the single most critical invariant in the system. Example-based tests cover specific transitions but cannot verify that ALL transition sequences maintain invariants. A single missed invalid transition can cause silent state corruption. |
| **Complexity** | Medium |
| **Existing Code** | `VALID_TRANSITIONS` dict in `domain/model/provider.py` defines the state machine cleanly. `ProviderState` enum has 5 states. `EventSourcedProvider` replays events to rebuild state. `_transition_to()` validates transitions and emits events. |
| **Gap** | No property-based tests exist. Only example-based unit tests for specific transitions. |
| **What's Needed** | Hypothesis stateful testing that generates random sequences of operations (start, stop, health check pass/fail, degrade, recover) and verifies: (a) invalid transitions always raise `InvalidStateTransitionError`, (b) every valid transition emits `ProviderStateChanged` event, (c) event replay reproduces identical state, (d) no reachable state has undefined behavior. |

---

## Differentiators

Features that go beyond baseline production-readiness. Not required, but significantly improve operational confidence.

| Feature | Value Proposition | Complexity | Dependencies | Notes |
|---------|------------------|------------|--------------|-------|
| Saga compensation on partial failure | When a multi-step saga fails mid-way, automatically undo completed steps (e.g., rebalance that moved 2 of 5 providers). Without this, operators must manually clean up partial state. | High | Saga persistence (table stakes #1) | `SagaState.COMPENSATING`/`COMPENSATED` states exist but no compensation logic is implemented. Requires persistent checkpoint state to know what to undo. |
| HALF_OPEN circuit breaker state | Allow controlled probe requests through a tripped breaker to detect recovery. Current OPEN-to-CLOSED jump on timeout is abrupt and can cause request spikes. | Low | Circuit breaker persistence (table stakes #2) | Small code change: add HALF_OPEN state, allow single probe request, close on success / re-open on failure. Well-understood pattern. |
| Snapshot compaction (old event pruning) | After snapshot creation, old events can be archived or deleted to bound storage growth. Without this, event store grows forever even with snapshots. | Medium | Event store snapshots (table stakes #3) | Only valuable after snapshots work. Separate concern -- snapshots stop read-time growth, compaction stops write-time growth. |
| Rate limit metrics and observability | Emit Prometheus metrics for rate limit hits, rejections, and bucket utilization. Without this, operators can't tell if rate limits are too strict or too loose. | Low | Rate limiter (table stakes #6) | `RateLimitResult` already has structured data. Need counters/histograms wired to `PrometheusMetricsPublisher`. |
| Fuzz testing for event deserialization | Ensure event replay is resilient to malformed or corrupted event data. Catches edge cases in `from_dict()` and event upcaster paths. | Medium | Event store, property-based testing infrastructure | Complements property-based testing. Different focus: testing validates logic invariants, fuzzing validates data resilience. |

---

## Anti-Features

Features to explicitly NOT build in this production hardening milestone.

| Anti-Feature | Why Avoid | What to Do Instead |
|-------------|-----------|-------------------|
| Distributed saga coordination (cross-node) | MCP Hangar is single-process by design. Distributed sagas (2PC, Raft-based) add massive complexity for zero benefit in current architecture. | Persist saga state locally (SQLite). Multi-node is a separate milestone if ever needed. |
| External circuit breaker service (Resilience4j sidecar, etc.) | Over-engineering. Adds operational dependency and network hop for a simple state machine. | Use existing in-process `CircuitBreaker` with persistence to SQLite or event store. |
| Real-time event streaming / event bus replication | Not needed for single-process hardening. Adds infrastructure requirements (Kafka, NATS) without matching need. | Snapshots + bounded event log is sufficient. Streaming is a scale-out concern for a future milestone. |
| ML-based anomaly detection for health checks | Exponential backoff with jitter covers 99% of real-world cases. ML adds complexity, unpredictability, and debugging difficulty without proportional value. | Deterministic backoff with configurable thresholds. Operators understand `min(60, 2^n * jitter)`. They don't understand ML models. |
| Per-request command allowlist UI / admin panel | Scope creep. The allowlist should be configuration-driven (YAML), not require a separate UI. | Use YAML config for allowed commands. CLI can validate config correctness. |
| Async/asyncio rewrite of domain layer | `CLAUDE.md` explicitly states "Don't use asyncio in core domain (thread-based by design)." The domain layer works. Rewriting it doesn't harden it. | Keep thread-based. Focus hardening on persistence, resilience, and testing -- not rewriting working code. |
| Custom snapshot serialization format | Inventing a binary format for snapshots adds complexity and debugging difficulty. | Use JSON serialization via existing `to_dict()`/`from_dict()` methods. Optimize only if profiling shows serialization is a bottleneck (it won't be). |

---

## Feature Dependencies

```text
Saga persistence (#1) ---------> Saga compensation (differentiator)
                                   (compensation needs persistent checkpoints to know what to undo)

Event store snapshots (#3) ----> Snapshot compaction (differentiator)
                                   (can't compact without snapshots to restore from)

Circuit breaker persistence (#2) -> HALF_OPEN state (differentiator)
                                   (HALF_OPEN probe state should persist across restarts too)

Rate limiter middleware (#6) --> Rate limit metrics (differentiator)
                                   (metrics should reflect transport-agnostic enforcement)

Property-based testing (#7) ---> Fuzz testing (differentiator)
                                   (testing infrastructure and patterns carry over)
```

**No circular dependencies among table stakes.** All 7 table-stakes features can be implemented independently and in parallel. The dependency arrows above show what must come before differentiators.

**Internal codebase dependencies (existing code each feature touches):**

| Feature | Primary Files Modified | Shared Dependencies |
|---------|----------------------|---------------------|
| Saga persistence | `infrastructure/saga_manager.py`, saga implementations, bootstrap | `IEventStore` or SQLite |
| Circuit breaker persistence | `domain/model/circuit_breaker.py`, bootstrap | SQLite or file storage |
| Event store snapshots | `domain/contracts/event_store.py`, `infrastructure/persistence/sqlite_event_store.py`, `infrastructure/event_sourced_repository.py` | `IEventStore` contract |
| Health check backoff | `domain/model/health_tracker.py`, `gc.py` | Provider state awareness |
| Command validation | `infrastructure/discovery/docker_source.py`, discovery pipeline | `InputValidator` |
| Rate limit middleware | `infrastructure/command_bus.py` or application services, `server/validation.py` | `InMemoryRateLimiter` |
| Property-based testing | `tests/` (new test files) | `Provider`, `VALID_TRANSITIONS`, `EventSourcedProvider` |

---

## MVP Recommendation

### Phase 1 -- Foundation (highest risk, highest value, do first)

1. **Event store snapshots** (#3) -- Unblocks unbounded growth problem. Domain plumbing already exists (`ProviderSnapshot`, `_create_snapshot`). Primary work: `IEventStore` contract update + SQLite snapshots table. High complexity but well-defined scope.
2. **Saga persistence** (#1) -- Highest complexity and highest impact. Without it, any restart loses in-flight recovery/failover state with no trace. `SagaContext.to_dict()` exists. Primary work: persistence backend + checkpoint triggers + resume logic.

### Phase 2 -- Safety (close security and resilience gaps)

3. **Command validation for discovery** (#5) -- Security gap. Smallest scope: wire existing `InputValidator` into discovery pipeline. `validate_command()` works, just needs calling from `DockerDiscoverySource`.
4. **Transport-agnostic rate limiting** (#6) -- Enforcement gap. Domain implementation complete. Primary work: command bus middleware or application service decorator to move enforcement inward.
5. **Circuit breaker persistence** (#2) -- Simple state, `to_dict()` exists, low risk. Natural companion to saga persistence (same storage patterns).

### Phase 3 -- Reliability and Confidence (validates everything above)

6. **Exponential backoff with jitter** (#4) -- `HealthTracker` needs jitter formula. `BackgroundWorker` needs state-aware scheduling. Medium complexity, well-understood patterns.
7. **Property-based state machine testing** (#7) -- Validates the state machine that all other features depend on. Catches edge cases in transitions. Best done last because it can also test the hardened code paths.

### Defer to later milestone

- Saga compensation, HALF_OPEN circuit breaker, snapshot compaction, rate limit metrics, fuzz testing

### Ordering Rationale

- **Foundation first** because event store snapshots and saga persistence are highest-risk, highest-complexity, and most likely to surface design issues. Start them when there's maximum time to iterate.
- **Safety second** because these close real security/resilience gaps (command injection, rate limit bypass) with lower implementation risk -- the code already exists and just needs wiring.
- **Confidence last** because testing and backoff improvements validate and refine everything built before them. Property-based testing is most valuable when it can exercise hardened code paths.

---

## Complexity Summary

| # | Feature | Complexity | Risk | Key Effort |
|---|---------|-----------|------|------------|
| 1 | Saga persistence & checkpointing | High | Medium | Serialization exists; need persistence backend, checkpoint triggers, resume-on-bootstrap, idempotent step execution |
| 2 | Circuit breaker persistence | Medium | Low | `to_dict()` exists; need save/restore hooks, storage backend |
| 3 | Event store snapshots | High | Medium | Domain snapshot exists; need `IEventStore` contract changes, SQLite table, snapshot-aware replay |
| 4 | Exponential backoff + jitter | Medium | Low | Backoff formula exists; need jitter, state-aware scheduling in `BackgroundWorker` |
| 5 | Command validation for discovery | Medium | Low | `InputValidator` exists; need wiring into discovery sources |
| 6 | Transport-agnostic rate limiting | Medium | Low | Rate limiter exists in domain; need command bus middleware or decorator |
| 7 | Property-based state machine testing | Medium | Low | `VALID_TRANSITIONS` clean dict; Hypothesis stateful testing well-documented |

---

## Sources

- **Direct codebase analysis** of 20+ source files across domain, application, infrastructure, and server layers (HIGH confidence)
- `CLAUDE.md` project instructions -- architecture constraints, forbidden patterns, layer rules (HIGH confidence)
- `.planning/PROJECT.md` milestone context, constraints, and design decisions (HIGH confidence)
- All findings verified against actual source code, not documentation or comments alone
