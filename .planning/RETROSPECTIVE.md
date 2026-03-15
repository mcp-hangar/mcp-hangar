# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v1.0 -- Production Hardening

**Shipped:** 2026-03-08 | **Released as:** v0.11.0 (2026-03-09)
**Phases:** 3 | **Plans:** 12 | **Sessions:** ~2

### What Was Built

- Concurrency safety fixes: ProviderGroup two-phase lock pattern, Provider threading.Event coordination for cold starts, StdioClient request-response ordering invariant
- Exception hygiene audit: 42 bare `except Exception:` catches categorized (fault-barrier, cleanup, bug-hiding) and resolved with structured conventions
- Discovery command validation against allowlist via InputValidator injection
- Saga persistence foundation: SagaStateStore (SQLite-backed) with checkpoint/load/is_processed, saga serialization (to_dict/from_dict) across all EventTriggeredSaga subclasses
- Circuit breaker persistence: CB from_dict() classmethod, ProviderSnapshot with CB state fields, shutdown save/startup restore
- Idempotency filter in SagaManager preventing duplicate command emission during event replay
- Event store snapshots: IEventStore save_snapshot/load_snapshot, SQLiteEventStore and InMemoryEventStore implementations, aggregate replay from latest snapshot
- Health check backoff with jitter (10% default), state-aware BackgroundWorker scheduling (normal for READY, backoff for DEGRADED, longer ceiling for DEAD, skip for COLD)
- CommandBus middleware pipeline with chain-of-responsibility pattern, RateLimitMiddleware for transport-independent rate limiting
- Docker discovery automatic reconnection with inline exponential backoff, graceful degradation on persistent failure
- Property-based state machine tests with Hypothesis RuleBasedStateMachine (11 valid + 9 invalid transitions)
- PEP 561 py.typed marker, mypy strictness (check_untyped_defs, no_implicit_optional, disallow_incomplete_defs), all type errors fixed

### What Worked

- Single-day execution for entire 3-phase milestone (12 plans) -- clear research documents and focused scope eliminated planning ambiguity
- TDD pattern (failing tests first, then implementation) caught regressions immediately, especially for concurrency fixes where race conditions are hard to reproduce
- Research phase (08-RESEARCH.md, 09-RESEARCH.md) before planning identified all 42 bare catches and all concurrency hazards upfront -- no surprises during execution
- Convention-based resolution for exception hygiene (fault-barrier vs infra-boundary annotations) was pragmatic -- optional dependencies make narrowing unsafe, so documenting intent is the right trade-off
- Reusing existing infrastructure (SQLiteConnectionFactory, MigrationRunner) for saga and CB persistence avoided new dependencies
- Plan durations consistently short (3-16 min per plan) indicating well-scoped tasks

### What Was Inefficient

- Phase 08 verification was not formally written (no 08-VERIFICATION.md) -- the only phase across all milestones missing this artifact
- Some decisions accumulated heavily in STATE.md (20+ decisions for v1.0) -- could benefit from grouping by phase rather than flat list
- Saga checkpoint fires after saga.handle() but before command dispatch, meaning command execution failures are invisible to the persistence layer -- acceptable for v1.0 but needs reconsideration for saga compensation (PERS-06)

### Patterns Established

- Two-phase lock pattern: snapshot under lock, I/O outside lock, re-acquire to update state
- threading.Event coordination for concurrent startup waiters (not condition variable)
- Multi-lock-cycle pattern for invoke_tool() following health_check() reference
- Fault-barrier / infra-boundary annotation convention for broad exception catches
- SagaStateStore checkpoint/load/is_processed API for saga persistence
- Idempotency guard: is_processed() before handle(), mark_processed() after checkpoint
- CB state saved at shutdown, restored at bootstrap (not per-transition)
- hasattr-based API detection for old/new event store compatibility
- CommandBus middleware chain-of-responsibility pattern
- Inline backoff in self-contained discovery sources (no cross-module imports)

### Key Lessons

1. Research-first planning is the single most impactful process improvement -- v1.0 research documents (08-RESEARCH, 09-RESEARCH) identified exact file locations, line numbers, and fix patterns, making plan execution mechanical
2. Convention documentation (fault-barrier annotations) is sometimes more valuable than code changes -- when language constraints prevent narrower typing, conventions prevent future regressions
3. CB state persistence at shutdown-only is a pragmatic trade-off -- avoids write amplification while covering the primary use case (cross-restart persistence)
4. Saga idempotency is non-trivial -- the interaction between event replay, checkpoint state, and command dispatch required careful ordering (is_processed before handle, mark_processed after checkpoint)
5. Property-based testing with Hypothesis found no new bugs but provided confidence in state machine correctness -- its value is proportional to the complexity of the state space

### Cost Observations

- Sessions: ~2
- Notable: Fastest milestone relative to scope (12 plans in ~1 day vs 7 plans in ~0.78h for v0.9). Research-driven scope and TDD eliminated debugging time.

---

## Milestone: v0.10 -- Documentation & Kubernetes Maturity

**Shipped:** 2026-03-01
**Phases:** 3 | **Plans:** 6 | **Sessions:** 4

### What Was Built

- Configuration Reference page (523 lines, 13 YAML sections, 28+ env vars) and MCP Tools Reference page (897 lines, 22 tools, 7 categories)
- Provider Groups Guide (355 lines, 5 strategies, health policies, circuit breaker, tool filtering) and Facade API Guide (430 lines, tabbed async/sync, HangarConfig builder)
- MCPProviderGroup controller (read-only aggregation, label selection, threshold-based health, 3 independent conditions)
- MCPDiscoverySource controller (4 discovery modes, additive/authoritative sync, owner references, partial failure tolerance)
- envtest integration test suite (12 tests: 6 group + 6 discovery) with TestMain-based setup
- Both Helm charts synchronized to v0.10.0 with NOTES.txt and test templates

### What Worked

- Phase research before planning consistently produced focused, well-scoped plans with minimal rework
- Atomic task commits with pre-commit hooks caught formatting issues early (markdownlint MD046, check-yaml excludes)
- Read-only aggregation pattern for MCPProviderGroup kept complexity low while delivering full status visibility
- Scoped authoritative deletion (only delete from successfully-scanned sources) provided safety-first semantics without overcomplicating sync
- envtest with testify (no Ginkgo/Gomega) kept tests readable and consistent with project convention
- Documentation plans completed in ~4 minutes each -- structured card format (params, returns, side effects) made content generation systematic

### What Was Inefficient

- `summary-extract --fields one_liner` returned null for all summaries during milestone completion -- the one_liner field wasn't populated in frontmatter, requiring manual extraction from `provides:` fields
- Milestone completion workflow has many sequential steps that could be parallelized (retrospective + state update + roadmap reorganization are independent)
- Phase 6 Plan 3 (envtest) took ~67 minutes vs 2-3 minutes for other plans -- conflict errors in annotation-triggered reconcile tests required debugging and retry patterns

### Patterns Established

- Tool card format: description, parameters table, side effects, returns table, JSON example
- Config section format: heading, description, YAML snippet, key/type/default/range table
- Tabbed async/sync pattern with `markdownlint-disable MD046` for pymdownx.tabbed compatibility
- Read-only aggregation controller pattern: select by label, aggregate status, evaluate thresholds
- Three independent conditions (Ready/Degraded/Available) with distinct semantics
- Discovery mode dispatch: switch on spec.Type, each mode returns (map, errors) independently
- Scoped deletion: authoritative sync only deletes providers from successfully-scanned sources
- Helm test pattern: busybox wget --spider to service endpoint with timeout
- NOTES.txt pattern: static text with Release/Values template refs only

### Key Lessons

1. When documentation covers internal APIs, use source code as authority over planning documents (7 tool categories from source files beat 6 from CONTEXT.md)
2. envtest conflict errors are common when tests trigger reconciliation while also modifying the same resource -- always use `require.Eventually` with retry for annotation/status updates
3. Helm template YAML files will always fail generic YAML linters -- maintain pre-commit exclude patterns proactively when adding new chart template directories
4. Static NOTES.txt content (no Go conditionals) is simpler to maintain and test than dynamic templates for early-stage charts

### Cost Observations

- Sessions: 4
- Notable: Documentation phases executed fastest (~4min each), Kubernetes controller phases required more debugging time for envtest integration

---

## Milestone: v0.9 -- Security Hardening

**Shipped:** 2026-02-15
**Phases:** 4 | **Plans:** 7 | **Sessions:** ~3

### What Was Built

- Constant-time API key validation (hmac.compare_digest) across all 4 auth stores
- Exponential backoff rate limiting (2x escalation, capped at 1h) with domain events
- JWT max token lifetime enforcement (configurable, default 3600s)
- Zero-downtime API key rotation with grace period across InMemory, SQLite, Postgres, and EventSourced stores

### What Worked

- Small, focused phases (1-2 plans each) completed rapidly -- 0.78 hours total execution
- Security audit document (AUTH_SECURITY_AUDIT.md) provided clear gap analysis that directly mapped to phases
- Domain event pattern (RateLimitLockout/Unlock, KeyRotated) kept audit trail consistent with existing architecture
- Value object pattern (max_token_lifetime=0 as escape hatch) provided clean API design

### What Was Inefficient

- Phase 4 (API Key Rotation) took longest (14.3min, 7.2min/plan avg) compared to others (~3-4min avg) -- cross-store coordination inherently more complex
- Some plans overlapped in touching the same files (rate_limiter.py modified in both Phase 2 plans)

### Patterns Established

- hmac.compare_digest for ALL hash comparisons (not just API keys)
- Iterate all dict entries without early exit to prevent timing side-channels
- Dummy hash comparison for SQL stores as defense-in-depth
- event_publisher optional callback pattern for backward-compatible event emission
- Cascading rotation prevention (block rotate if grace period active)

### Key Lessons

1. Security hardening is best done as a focused milestone with audit-driven scope -- the AUTH_SECURITY_AUDIT.md made phase decomposition trivial
2. Cross-store concerns (rotation across InMemory/SQLite/Postgres/EventSourced) multiply testing surface -- budget extra time
3. Escape hatches (max_token_lifetime=0 disables check) should be explicit design decisions, not afterthoughts

### Cost Observations

- Sessions: ~3
- Notable: Fastest milestone to date -- 0.78 hours total. Small focused phases with clear security audit guidance eliminated ambiguity

---

## Milestone: v4.0 -- Log Streaming

**Shipped:** 2026-03-15
**Phases:** 2 | **Plans:** 6 | **Files changed:** 31 files, +3,349/-100 lines

### What Was Built

- `LogLine` frozen dataclass and `IProviderLogBuffer` ABC in `domain/value_objects/`; `ProviderLogBuffer` with `collections.deque(maxlen=1000)` ring buffer; thread-safe `get_or_create_log_buffer()` singleton registry with `_registry_lock`
- Daemon stderr-reader threads added to `SubprocessLauncher` and `DockerLauncher`; `DockerLauncher` changed from `stderr=subprocess.DEVNULL` to `stderr=subprocess.PIPE` -- the critical change that made live capture possible; all threads terminate cleanly on process exit via `thread.daemon = True`
- `GET /api/providers/{id}/logs` REST endpoint with `lines` clamping [1, 1000], 404 for unknown providers, empty list for cold/unstarted providers (tolerant parsing for non-critical param)
- `LogStreamBroadcaster` with per-provider `dict[str, Callable]` registry of async callbacks; `on_append` invoked outside `ProviderLogBuffer._lock` per no-I/O-under-lock rule; WebSocket endpoint `GET /api/ws/providers/{id}/logs` sends buffered history on connect then streams live lines; `try/finally` cleanup removes callback on any disconnect path
- Bootstrap wires `LogStreamBroadcaster` singleton on `ApplicationContext` and injects `ProviderLogBuffer` per configured provider via deferred injection (avoids `Provider` constructor signature change)
- `LogViewer` React component (monospace, amber stderr, gray stdout); `useProviderLogs` hook with auto-reconnect matching existing `useWebSocket` hook pattern; `ProviderDetailPage` "Process Logs" section at bottom

### What Worked

- Focused 2-phase scope with clear layered dependency (capture layer first, streaming layer second) eliminated integration surprises -- Phase 22 only consumed what Phase 21 had already validated
- No-I/O-under-lock rule applied consistently at both the `ProviderLogBuffer` append path and the `LogStreamBroadcaster` notification path -- no deadlock risk
- Deferred buffer injection pattern (construct `Provider`, inject buffer after) avoided a breaking constructor change and kept bootstrap as the only composition root
- `useProviderLogs` hook reused the existing `useWebSocket` auto-reconnect pattern -- no new hook infrastructure needed
- 81/81 log-related unit tests passing at completion -- small surface area, focused tests

### What Was Inefficient

- Phase 22 had no `PLAN.md` files (only SUMMARY.md) -- plans were pre-committed before the session started, requiring summaries to be read for context instead of plans
- `SubprocessLauncher` vs `ContainerLauncher` naming inconsistency in requirements vs actual code (`DockerLauncher`) required clarification during plan execution

### Patterns Established

- `deque(maxlen=N)` ring buffer for bounded per-entity history (reusable for other provider-scoped buffers)
- `get_or_create_log_buffer()` with `_registry_lock` for thread-safe idempotent singleton creation
- Deferred injection pattern: construct aggregate first, inject dependencies after via bootstrap
- Per-entity broadcaster dict with `try/finally` cleanup for WebSocket fanout without leaks
- `daemon=True` reader threads that terminate naturally when the monitored process exits
- BLE001 fault-barrier in I/O reader threads -- pipe errors on process kill must not crash the thread

### Key Lessons

1. The "silent crash" problem (DEVNULL) was a single-line fix that had been missed for the entire project lifetime -- small targeted changes with high diagnostic value are worth prioritizing even when scope is constrained
2. No-I/O-under-lock must be applied at every layer in a notification chain -- `ProviderLogBuffer.append()` releases lock before calling `on_append`, and `on_append` must not block (async dispatch)
3. Deferred injection is cleaner than constructor parameters for infrastructure concerns that bootstrap wires -- keeps domain aggregates free of infrastructure dependencies
4. Reusing existing hook patterns (`useWebSocket` auto-reconnect) rather than inventing new ones kept the frontend consistent and reduced review surface

### Cost Observations

- Files changed: 31, +3,349/-100 lines
- Timeline: started 2026-03-14, shipped 2026-03-15
- Notable: Smallest milestone by phase count (2) and file count (31). Highest value-to-effort ratio -- DEVNULL-to-PIPE was a one-line change that unlocked the entire feature.

---

## Milestone: v3.0 -- Infrastructure Maturity

**Shipped:** 2026-03-14
**Phases:** 4 | **Plans:** 11

### What Was Built

- Ruff BLE001 lint rule enabled project-wide; all ~100 fault-barrier `except Exception` sites annotated with `# noqa: BLE001` inline justification; four REST API JSON-parsing catches narrowed to `json.JSONDecodeError | ValueError`
- `RATE_LIMIT_HITS_TOTAL` counter with `result` label (`allowed`|`rejected`); `RATE_LIMIT_ACTIVE_BUCKETS` gauge from `InMemoryRateLimiter.get_stats()`; auth rate limiter increments counter on lockout
- Hypothesis `@given`-based fuzz tests for event deserialization: arbitrary bytes/dicts, unknown keys, round-trip for all 17 event types
- `CircuitBreaker` full three-state machine (`CLOSED`, `OPEN`, `HALF_OPEN`) with `probe_count` config; `CircuitBreakerStateChanged` domain event; `mcp_hangar_circuit_breaker_state` Gauge; old snapshot backward compat
- `IEventStore.compact_stream()` on both backends; `CompactionError` when no snapshot; `POST /api/maintenance/compact` admin endpoint; `mcp_hangar_events_compacted_total` counter
- `ProviderFailoverSaga` converted to step-based `Saga` with named `SagaStep` entries and `compensation_command` fields; `SagaManager.schedule_command(command, delay_s)` replacing inline TODO schedulers; integration tests covering COMPENSATING → COMPENSATED path
- `MetricsHistoryStore` SQLite backend with 60s snapshot worker, configurable retention pruning, `GET /api/metrics/history` endpoint; MetricsPage time-series line chart with 1h/6h/24h/7d range selector
- D3.js force-directed topology graph at `/topology`; state-colored provider nodes; group nodes; WebSocket-driven updates; node click navigation

### What Worked

- Deferred requirements closure pattern: all six v1.0 deferred items resolved in a single milestone -- stacking them ensured none rotted in a backlog indefinitely
- "Quick Wins" phase (EXCP-02, RESL-04, TEST-02) as phase 17 reduced risk for the heavier infrastructure work in phases 18-19

### Patterns Established

- `HALF_OPEN` probe state: probe failure resets to `OPEN` with fresh `opened_at` (not a time extension)
- `compact_stream()` raises `CompactionError` without a snapshot reference -- safety gate on destructive operation
- `SagaStep.compensation_command` as first-class field, not a comment
- `MetricsHistoryStore` snapshot-every-N-seconds with pruning worker pattern

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Sessions | Phases | Plans | Key Change |
|-----------|----------|--------|-------|------------|
| v0.9 | ~3 | 4 | 7 | Audit-driven scope, rapid execution |
| v0.10 | 4 | 3 | 6 | Research-first planning, mixed Go/Python/Docs work |
| v1.0 | ~2 | 3 | 12 | Research-driven execution, TDD pattern, convention documentation |
| v2.0 | ~1 | 6 | 34 | Largest milestone by plan count; full UI stack introduced in single day |
| v3.0 | ~1 | 4 | 11 | Deferred-requirements closure pattern; stacked v1.0 deferrals resolved together |
| v4.0 | ~1 | 2 | 6 | Smallest milestone; highest value-to-effort ratio (DEVNULL-to-PIPE was core unlock) |

### Velocity

| Milestone | Total Time | Files Changed | Lines Added | Avg Plan Duration |
|-----------|-----------|---------------|-------------|-------------------|
| v0.9 | 0.78h | 30 | +5,012 | 4.7min |
| v0.10 | ~2 days | 40 | +8,766 | varies (2-67min) |
| v1.0 | ~1 day | 107 | +5,073 | ~6min |
| v2.0 | ~1 day | ~150 | ~+18,000 | varies |
| v3.0 | ~1 day | ~80 | ~+4,000 | ~6min |
| v4.0 | ~2 days | 31 | +3,349 | ~8min |

### Top Lessons (Verified Across Milestones)

1. Focused phases with clear scope (audit or research-driven) execute fastest with least rework
2. Domain event emission for all state changes provides consistent audit trail and enables future features (monitoring, alerting) without retrofitting
3. Pre-commit hooks catch issues early but require proactive exclude pattern maintenance when adding new file types or directories
4. Research-first planning is consistently the highest-impact process improvement -- every milestone that invested in upfront research executed faster with fewer surprises
5. TDD (failing tests first) is especially valuable for concurrency and state machine work where bugs are hard to reproduce after the fact
6. Deferred-requirements closure (stacking multiple deferrals into one milestone) prevents backlog rot -- v3.0 proved this by resolving all six v1.0 deferred items at once
7. Smallest scopes can deliver highest value -- v4.0's core unlock was a one-line DEVNULL-to-PIPE change; phase decomposition by layer (capture then stream) eliminated integration surprises
8. Deferred injection (construct aggregate first, inject infrastructure dependencies after via bootstrap) keeps domain aggregates clean and avoids constructor signature churn when adding new capabilities
