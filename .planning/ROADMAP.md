# Roadmap: MCP Hangar

## Milestones

- ✅ **v0.9 Security Hardening** -- Phases 1-4 (shipped 2026-02-15)
- ✅ **v0.10 Documentation & Kubernetes Maturity** -- Phases 5-7 (shipped 2026-03-01)
- ✅ **v1.0 Production Hardening** -- Phases 8-10 (shipped 2026-03-08, released as v0.11.0: 2026-03-09)
- ✅ **v2.0 Management UI** -- Phases 11-16 (shipped 2026-03-14)
- ✅ **v3.0 Infrastructure Maturity** -- Phases 17-20 (shipped 2026-03-14)
- **v4.0 Log Streaming** -- Phases 21-22 (shipped 2026-03-15)

## Phases

<details>
<summary>✅ v0.9 Security Hardening (Phases 1-4) -- SHIPPED 2026-02-15</summary>

- [x] Phase 1: Timing Attack Prevention (2/2 plans) -- completed 2026-02-15
- [x] Phase 2: Rate Limiter Hardening (2/2 plans) -- completed 2026-02-15
- [x] Phase 3: JWT Lifetime Enforcement (1/1 plan) -- completed 2026-02-15
- [x] Phase 4: API Key Rotation (2/2 plans) -- completed 2026-02-15

</details>

<details>
<summary>✅ v0.10 Documentation & Kubernetes Maturity (Phases 5-7) -- SHIPPED 2026-03-01</summary>

- [x] Phase 5: Documentation Content (2/2 plans) -- completed 2026-02-28
- [x] Phase 6: Kubernetes Controllers (3/3 plans) -- completed 2026-03-01
- [x] Phase 7: Helm Chart Maturity (1/1 plan) -- completed 2026-03-01

</details>

<details>
<summary>✅ v1.0 Production Hardening (Phases 8-10) -- SHIPPED 2026-03-08</summary>

- [x] Phase 8: Safety Foundation (3/3 plans) -- completed 2026-03-08
- [x] Phase 9: State Survival (3/3 plans) -- completed 2026-03-08
- [x] Phase 10: Operational Hardening (6/6 plans) -- completed 2026-03-08

</details>

<details>
<summary>✅ v2.0 Management UI (Phases 11-16) -- SHIPPED 2026-03-14</summary>

**Milestone Goal:** Add a browser-based management UI for MCP Hangar with full provider lifecycle control, real-time event streaming, metrics dashboards, auth management, and configuration visibility -- backed by a REST API layer wrapping existing CQRS infrastructure and WebSocket streaming from the EventBus.

- [x] **Phase 11: Backend REST API** - Starlette routes wrapping CQRS commands/queries for providers, groups, auth, config, discovery, events, metrics, audit, and system endpoints (completed 2026-03-14)
- [x] **Phase 12: WebSocket Infrastructure** - Connection manager, EventBus subscription bridge (sync-to-async queue), event streaming channel, state snapshot channel (completed 2026-03-14)
- [x] **Phase 13: Frontend Foundation** - Vite + React + TypeScript project, routing, API client with TanStack Query, WebSocket hooks with auto-reconnect, layout shell, component library (completed 2026-03-14)
- [x] **Phase 14: Dashboard & Provider Management** - Dashboard page, provider list/detail, group management, discovery management, configuration viewer (completed 2026-03-14)
- [x] **Phase 15: Observability & Operations** - Metrics dashboards with Recharts, event stream viewer, audit log, execution history, security events (completed 2026-03-14)
- [x] **Phase 16: Auth, Config & Production** - API key management, role management, production static build serving, SPA routing fallback, Docker multi-stage build (completed 2026-03-14)

</details>

## Phase Details

<details>
<summary>Phase 8-10 Details (v1.0 -- Complete)</summary>

### Phase 8: Safety Foundation

**Goal**: The codebase holds locks correctly, propagates exceptions specifically, and validates commands from untrusted discovery sources -- establishing the trustworthy foundation all subsequent hardening builds on
**Depends on**: Phase 7 (prior milestone)
**Requirements**: CONC-01, CONC-02, CONC-03, CONC-04, EXCP-01, SECR-01
**Success Criteria** (what must be TRUE):

  1. ProviderGroup operations that start member providers never hold the group lock while acquiring a provider lock -- the level-11-holds-level-10 deadlock path is eliminated
  2. Provider cold starts (subprocess launch, tool discovery) perform all I/O outside the provider lock, using INITIALIZING state guard so concurrent callers wait via threading.Event instead of blocking on the lock
  3. StdioClient request-response matching is race-free -- PendingRequest is registered before the request is written to stdin, so no response can arrive before its handler exists
  4. All 42 bare `except Exception:` catches are resolved -- fault-barriers kept with structured logging, cleanup paths narrowed to specific exceptions, bug-hiding catches removed or replaced
  5. Provider commands sourced from Docker labels or Kubernetes annotations are validated against a command allowlist before registration, preventing command injection from untrusted discovery sources
**Plans**: 3 plans
Plans:

- [x] 08-01-PLAN.md -- Quick wins + security + group lock fix (CONC-04, SECR-01, CONC-01) -- completed 2026-03-08
- [x] 08-02-PLAN.md -- Provider concurrency refactor (CONC-02, CONC-03) -- completed 2026-03-08
- [x] 08-03-PLAN.md -- Exception hygiene audit (EXCP-01) -- completed 2026-03-08

### Phase 9: State Survival

**Goal**: Saga and circuit breaker state survives process restarts -- incomplete sagas resume from their last checkpoint and circuit breakers remember known-bad providers
**Depends on**: Phase 8
**Requirements**: PERS-01, PERS-02, PERS-03
**Success Criteria** (what must be TRUE):

  1. Saga state is checkpointed to SQLite after each step transition, and incomplete sagas are detected and resumed on bootstrap without emitting duplicate commands during event replay
  2. Circuit breaker state (state, failure_count, opened_at) persists in provider snapshots and is restored on restart, so previously-tripped breakers remain open against known-bad providers
  3. Both saga checkpoints and circuit breaker state use the existing SQLiteConnectionFactory and MigrationRunner infrastructure -- no new database connections or migration systems
**Plans**: 3 plans
Plans:

- [x] 09-01-PLAN.md -- Saga persistence foundation (PERS-01) -- completed 2026-03-08
- [x] 09-02-PLAN.md -- Circuit breaker persistence (PERS-03) -- completed 2026-03-08
- [x] 09-03-PLAN.md -- Idempotency filter + bootstrap wiring (PERS-02, PERS-03) -- completed 2026-03-08

### Phase 10: Operational Hardening

**Goal**: Startup time is bounded by snapshots, health checks use intelligent backoff, rate limiting covers all entry points, Docker discovery self-heals, and the core state machine is verified by property-based tests with strict typing
**Depends on**: Phase 9
**Requirements**: PERS-04, PERS-05, SECR-02, RESL-01, RESL-02, RESL-03, TEST-01, QUAL-01
**Success Criteria** (what must be TRUE):

  1. IEventStore supports snapshots and aggregate replay loads from latest snapshot plus subsequent events, bounding startup time regardless of total event history
  2. Health checks use exponential backoff with jitter for degraded providers and BackgroundWorker schedules checks based on provider state
  3. Rate limiting is enforced at the command bus middleware layer, covering stdio, HTTP, and programmatic callers uniformly regardless of transport
  4. Docker discovery source reconnects automatically with retry and exponential backoff when the Docker daemon connection is lost
  5. Property-based tests using Hypothesis RuleBasedStateMachine verify that all Provider state transition sequences maintain invariants, and the package includes py.typed with incrementally-enabled mypy strictness
**Plans**: 6 plans
Plans:

- [x] 10-01-PLAN.md -- Health check backoff with jitter (RESL-01, RESL-02) -- completed 2026-03-08
- [x] 10-02-PLAN.md -- Event store snapshots (PERS-04, PERS-05) -- completed 2026-03-08
- [x] 10-03-PLAN.md -- Rate limiter command bus middleware (SECR-02) -- completed 2026-03-08
- [x] 10-04-PLAN.md -- Docker discovery resilience (RESL-03) -- completed 2026-03-08
- [x] 10-05-PLAN.md -- Property-based testing (TEST-01) -- completed 2026-03-08
- [x] 10-06-PLAN.md -- Typing strictness + py.typed (QUAL-01) -- completed 2026-03-08

</details>

### Phase 11: Backend REST API

**Goal**: A complete REST API layer exists under `/api/` that wraps all existing CQRS commands and queries, providing JSON endpoints for provider lifecycle, groups, auth, config, discovery, events, metrics, audit, and system operations -- with consistent error handling and no new business logic
**Depends on**: Phase 10 (stable backend with persisted state, rate limiting, and typed interfaces)
**Requirements**: REST-01, REST-02, REST-03, REST-04, REST-05, REST-06, REST-07, REST-08, REST-09, REST-10, INTG-01
**Success Criteria** (what must be TRUE):

  1. REST endpoints exist for all provider lifecycle operations (list, get, start, stop), group operations (list, detail, rebalance), and tool listing per provider -- each wrapping existing CQRS handlers via `run_in_threadpool()`
  2. REST endpoints exist for auth management (API keys CRUD, roles CRUD, role assignments), configuration (get config, hot reload), and discovery (sources, pending, quarantined, approve/reject)
  3. REST endpoints exist for observability data (metrics as JSON, audit log with filters, security events, alert history, event store queries)
  4. All endpoints are mounted under `/api/` on the existing ASGI application without disrupting `/health`, `/ready`, `/metrics`, or `/mcp` routes
  5. Error responses use a consistent JSON envelope mapping domain exceptions to HTTP status codes
  6. CORS middleware is configured with environment-variable-driven allowed origins
  7. No sync CQRS operations run on the ASGI event loop -- all use `run_in_threadpool()`
**Plans**: 5 plans

Plans:

- [x] 11-01-PLAN.md -- API foundation + provider endpoints (REST-01, REST-03, REST-08, REST-09, REST-10, INTG-01) -- completed 2026-03-14
- [x] 11-02-PLAN.md -- Groups, discovery, config, and system endpoints (REST-02, REST-04, REST-05, REST-06, REST-07) -- completed 2026-03-14
- [x] 11-03-PLAN.md -- Tool invocation history endpoint + groups.py thread-safety fix (REST-03 gap) -- completed 2026-03-14
- [x] 11-04-PLAN.md -- Auth management endpoints: API keys and roles (REST-04 gap) -- completed 2026-03-14
- [x] 11-05-PLAN.md -- Observability endpoints: metrics, audit, security, alerts (REST-07 gap) -- completed 2026-03-14

### Phase 12: WebSocket Infrastructure

**Goal**: Real-time event streaming and state updates flow from the backend to browser clients over WebSocket, with clean connection lifecycle management and no resource leaks
**Depends on**: Phase 11 (REST API establishes ASGI integration patterns and serializers reused by WebSocket)
**Requirements**: WS-01, WS-02, WS-03, WS-04, WS-05
**Success Criteria** (what must be TRUE):

  1. A WebSocket endpoint at `/api/ws/events` streams domain events in real-time, with client-side subscription filters for event type, provider ID, and severity
  2. A WebSocket endpoint at `/api/ws/state` sends periodic provider/group state snapshots at a configurable interval
  3. EventBus has an `unsubscribe_from_all()` method and WebSocket disconnections trigger cleanup
  4. A thread-safe queue bridges sync EventBus handlers to async WebSocket broadcast
  5. Connection manager tracks active connections with ping/pong heartbeat for dead connection detection
**Plans**: 2 plans

Plans:

- [x] 12-01-PLAN.md -- EventBus.unsubscribe_from_all + WebSocket infrastructure (WS-03, WS-04, WS-05)
- [x] 12-02-PLAN.md -- WebSocket endpoints (events, state) + ASGI routing (WS-01, WS-02, WS-03, WS-05)

### Phase 13: Frontend Foundation

**Goal**: A React + TypeScript project exists with routing, typed API client, WebSocket hooks, and a layout shell -- the foundation all feature pages build on
**Depends on**: Phase 12 (WebSocket endpoints must exist for hook development and testing)
**Requirements**: UI-01, UI-02, UI-03, UI-04, INTG-03
**Success Criteria** (what must be TRUE):

  1. `packages/ui/` contains a Vite + React + TypeScript project with react-router routing for all 9 pages
  2. API client layer provides typed functions for all REST endpoints with TanStack Query integration (caching, background refetch)
  3. WebSocket hooks (`useWebSocket`, `useEventStream`, `useProviderState`) handle auto-reconnect with exponential backoff and trigger TanStack Query cache invalidation
  4. Layout shell with sidebar navigation, header with system status indicator, and content area is functional
  5. Vite dev server proxies `/api/*` to backend for CORS-free development
**Plans**: 3 plans

Plans:

- [x] 13-01-PLAN.md -- Vite + React + TypeScript scaffold, types, API client, TanStack Query keys (UI-01, UI-02, INTG-03)
- [x] 13-02-PLAN.md -- WebSocket hooks: useWebSocket, useEventStream, useProviderState + Zustand WS store (UI-03, INTG-03)
- [x] 13-03-PLAN.md -- Layout shell, routing (all 9 pages), sidebar nav, header with system status (UI-01, UI-04)

### Phase 14: Dashboard & Provider Management

**Goal**: Core management pages are functional -- dashboard with system overview, provider list/detail with lifecycle actions, group management, discovery management, and configuration viewer
**Depends on**: Phase 13 (layout shell, API client, and WebSocket hooks must be ready)
**Requirements**: UI-05, UI-06, UI-07, UI-08, UI-13, UI-14
**Success Criteria** (what must be TRUE):

  1. Dashboard shows provider state distribution chart, key metric cards, live event feed via WebSocket, and alert summary
  2. Provider list is filterable/sortable with state indicators and start/stop actions; detail view shows health, tools with schemas, circuit breaker, event timeline
  3. Group list shows strategy, member counts, circuit breaker; detail view has member list and rebalance action
  4. Discovery page shows source health, pending providers with approve/reject, quarantined providers
  5. Configuration page shows current config (read-only) and hot reload trigger
**Plans**: 4 plans

Plans:

- [x] 14-01-PLAN.md -- Shared UI primitives: ProviderStateBadge, HealthBadge, CircuitBreakerBadge, MetricCard, ActionButton, EmptyState, LoadingSpinner (UI-14)
- [x] 14-02-PLAN.md -- Dashboard page: metric cards, state distribution chart, live event feed, alert summary (UI-05)
- [x] 14-03-PLAN.md -- Provider list (filtered table + start/stop) + Provider detail (health, tools, circuit breaker) (UI-06)
- [x] 14-04-PLAN.md -- Groups list + detail + rebalance, Discovery sources + pending + quarantined, Config viewer + hot reload (UI-07, UI-08, UI-13)

### Phase 15: Observability & Operations

**Goal**: Operational visibility pages are functional -- metrics dashboards with charts, event stream viewer, audit log, execution history, and security events
**Depends on**: Phase 14 (dashboard patterns, chart components, and table components established)
**Requirements**: UI-09, UI-10, UI-11, UI-12
**Success Criteria** (what must be TRUE):

  1. Metrics page shows RED metrics per provider with Recharts visualizations and SLI availability/error budget
  2. Events page shows live WebSocket event stream with type/severity filters and paginated audit log with entity/time filters
  3. Executions page shows tool invocation timeline, failures view with error details, and success rate/p95 statistics
  4. Security events viewer shows security events with severity indicators
**Plans**: 3 plans

Plans:

- [x] 15-01-PLAN.md -- API client fixes + per-provider backend endpoint + MetricsPage (UI-09)
- [x] 15-02-PLAN.md -- EventsPage: live stream with type filter + paginated audit log (UI-10)
- [x] 15-03-PLAN.md -- ExecutionsPage + SecurityPage + /security route (UI-11, UI-12)

### Phase 16: Auth, Config & Production

**Goal**: Auth management UI is functional and the application is production-ready -- API key management, role management, static build serving with SPA fallback, and Docker multi-stage build
**Depends on**: Phase 15 (all feature pages complete, patterns established)
**Requirements**: UI-15, UI-16, INTG-02, INTG-04
**Success Criteria** (what must be TRUE):

  1. Auth page provides API key management (list, create, revoke) per principal
  2. Auth page provides role management (list builtin/custom, create custom) and role assignment (assign/revoke)
  3. Backend serves UI static build in production mode with SPA routing fallback for all non-API routes
  4. Multi-stage Docker build produces a single image with Python backend and UI static files
**Plans**: 3 plans

Plans:

- [x] 16-01-PLAN.md -- Auth API client fixes + AuthPage implementation (UI-15, UI-16) -- completed 2026-03-14
- [x] 16-02-PLAN.md -- Static build serving + SPA fallback (INTG-02) -- completed 2026-03-14
- [x] 16-03-PLAN.md -- Multi-stage Docker build with UI baked in (INTG-04) -- completed 2026-03-14

## v3.0 Infrastructure Maturity

**Milestone Goal:** Close all six requirements deferred from v1.0 (EXCP-02, PERS-06, PERS-07, PERS-08, RESL-04, TEST-02), extend observability depth with rate-limit metrics and circuit breaker instrumentation, implement snapshot compaction to bound event store growth, harden the circuit breaker with HALF_OPEN probe semantics, put saga compensation into production use, and promote two UI features (topology visualization, metric time-series) from the v2.0 out-of-scope list.

### Phase 17: Quick Wins

**Goal**: Close the three smallest deferred requirements in a single phase -- exception hygiene linting, rate-limit metric completeness, and fuzz testing for event deserialization -- so the codebase is cleaner and better-covered before the heavier infrastructure work in Phases 18-19.
**Depends on**: Phase 16 (v2.0 complete)
**Requirements**: EXCP-02, RESL-04, TEST-02
**Success Criteria** (what must be TRUE):

  1. Ruff BLE001 is listed in the `select` array in `pyproject.toml`. `ruff check` passes with zero BLE001 violations. All fault-barrier `except Exception` sites carry `# noqa: BLE001` with an inline justification. The four REST API JSON-parsing catches are narrowed to `json.JSONDecodeError | ValueError`.
  2. `RATE_LIMIT_HITS_TOTAL` counter carries a `result` label (`allowed` | `rejected`). `RATE_LIMIT_ACTIVE_BUCKETS` Gauge is defined and updated from `InMemoryRateLimiter.get_stats()`. `InMemoryRateLimiter.consume()` increments the counter. The auth rate limiter increments the counter on lockout. All existing wire points are updated to the new counter signature.
  3. `packages/core/tests/unit/test_event_deserialization_fuzz.py` exists with four `@given`-based tests covering arbitrary-input robustness, unknown-key tolerance, and serialize/deserialize round-trip for all 17 event types.

**Plans**: 3 plans

Plans:

- [x] 17-01-PLAN.md -- BLE001 enablement: enable rule, narrow 4 REST catches, add noqa suppressions to fault-barrier sites (EXCP-02)
- [x] 17-02-PLAN.md -- Rate limit metrics: add `result` label, ACTIVE_BUCKETS gauge, wire InMemoryRateLimiter + auth limiter (RESL-04)
- [x] 17-03-PLAN.md -- Fuzz tests: 4 @given tests for EventSerializer + UpcasterChain round-trip and adversarial inputs (TEST-02)

### Phase 18: Circuit Breaker & Event Store Compaction

**Goal**: The circuit breaker implements the full industry-standard three-state machine with probe semantics, and the event store supports compaction so event history does not grow without bound. Both changes are self-contained domain/infrastructure work with no saga dependencies.
**Depends on**: Phase 17
**Requirements**: PERS-07, PERS-08
**Success Criteria** (what must be TRUE):

  1. `CircuitState` enum has `CLOSED`, `OPEN`, `HALF_OPEN`. `CircuitBreaker.allow_request()` in `OPEN` state transitions to `HALF_OPEN` (not `CLOSED`) on timeout expiry, gates exactly `probe_count` requests, and resets to `OPEN` on probe failure or to `CLOSED` on probe success. `CircuitBreakerConfig.probe_count: int = 1` exists. Old snapshots without `HALF_OPEN` load without error.
  2. `mcp_hangar_circuit_breaker_state` Gauge (labels: `provider`, `state`) is defined in `metrics.py` and updated on every `CircuitBreaker` state transition. A `CircuitBreakerStateChanged` domain event is emitted on every transition.
  3. `IEventStore.compact_stream(stream_id)` is defined. `SQLiteEventStore` and `InMemoryEventStore` implement it. Compaction raises `CompactionError` when no snapshot exists. `POST /api/maintenance/compact` admin endpoint is wired. `mcp_hangar_events_compacted_total` counter is incremented on success.

**Plans**: 3 plans

Plans:

- [x] 18-01-PLAN.md -- CircuitBreaker HALF_OPEN state machine: new enum value, probe_count config, allow_request/record_success/record_failure logic (PERS-07)
- [x] 18-02-PLAN.md -- Circuit breaker instrumentation: CircuitBreakerStateChanged event, mcp_hangar_circuit_breaker_state Gauge, from_dict backward compat, snapshot persistence update (PERS-07)
- [x] 18-03-PLAN.md -- Snapshot compaction: IEventStore.compact_stream(), SQLite + InMemory implementations, CompactionError, admin endpoint, mcp_hangar_events_compacted_total counter (PERS-08)

### Phase 19: Saga Compensation

**Goal**: Saga compensation moves from implemented-but-unused infrastructure to a tested production pattern. `ProviderFailoverSaga` is converted to the step-based `Saga` class, the delayed-command facility is added to `SagaManager`, and integration tests verify the full compensation path.
**Depends on**: Phase 18 (circuit breaker changes affect provider group behavior that failover saga depends on)
**Requirements**: PERS-06
**Success Criteria** (what must be TRUE):

  1. `ProviderFailoverSaga` is a `Saga` subclass with at least three named `SagaStep` entries, each carrying a `compensation_command`. The `compensation_command` fields are not `None`.
  2. `SagaManager` has a `schedule_command(command, delay_s)` facility. `ProviderRecoverySaga` backoff delays and `ProviderFailoverSaga` failback delays use it instead of the current inline TODO comments.
  3. Integration tests in `tests/integration/test_saga_compensation.py` exercise: (a) successful failover end-to-end, (b) mid-saga failure triggers `_compensate_saga()` and all compensation commands are dispatched in reverse step order, (c) a compensated saga reaches `SagaState.COMPENSATED`.

**Plans**: 2 plans

Plans:

- [x] 19-01-PLAN.md -- SagaManager delayed-command facility + ProviderFailoverSaga step-based conversion with compensation commands (PERS-06)
- [x] 19-02-PLAN.md -- Saga compensation integration tests: happy path, mid-saga failure, full COMPENSATING -> COMPENSATED path (PERS-06)

### Phase 20: UI Enhancements

**Goal**: Two formerly out-of-scope UI features are delivered -- provider topology visualization and metric time-series persistence -- completing the v3.0 milestone.
**Depends on**: Phase 19 (all backend work must be stable before UI work; HALF_OPEN state and compaction endpoint referenced in UI)
**Requirements**: UI-17, UI-18
**Success Criteria** (what must be TRUE):

  1. A `/topology` route renders an interactive D3.js force-directed graph of providers and groups. Nodes are colored by provider state; group nodes are visually distinct. Clicking a node navigates to its detail page. Graph updates on WebSocket `state` channel messages.
  2. `MetricsHistoryStore` (SQLite-backed) records metric snapshots every 60 seconds. `GET /api/metrics/history` accepts `provider`, `metric`, `from`, `to` query params. The metrics page shows a time-range selector and switches to a time-series line chart for the selected range. A background worker prunes history older than configured retention (default 7 days).

**Plans**: 3 plans

Plans:

- [x] 20-01-PLAN.md -- MetricsHistoryStore (SQLite schema, snapshot worker, pruning worker) + GET /api/metrics/history endpoint (UI-18 backend)
- [x] 20-02-PLAN.md -- MetricsPage time-series: time-range selector, history API client, line chart replacing point-in-time chart (UI-18 frontend)
- [x] 20-03-PLAN.md -- Topology page: D3.js force-directed graph, provider/group nodes, state-colored nodes, WebSocket-driven updates, node click navigation (UI-17)

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> ... -> 20

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Timing Attack Prevention | v0.9 | 2/2 | Complete | 2026-02-15 |
| 2. Rate Limiter Hardening | v0.9 | 2/2 | Complete | 2026-02-15 |
| 3. JWT Lifetime Enforcement | v0.9 | 1/1 | Complete | 2026-02-15 |
| 4. API Key Rotation | v0.9 | 2/2 | Complete | 2026-02-15 |
| 5. Documentation Content | v0.10 | 2/2 | Complete | 2026-02-28 |
| 6. Kubernetes Controllers | v0.10 | 3/3 | Complete | 2026-03-01 |
| 7. Helm Chart Maturity | v0.10 | 1/1 | Complete | 2026-03-01 |
| 8. Safety Foundation | v1.0 | 3/3 | Complete | 2026-03-08 |
| 9. State Survival | v1.0 | 3/3 | Complete | 2026-03-08 |
| 10. Operational Hardening | v1.0 | 6/6 | Complete | 2026-03-08 |
| 11. Backend REST API | v2.0 | 5/5 | Complete | 2026-03-14 |
| 12. WebSocket Infrastructure | v2.0 | 2/2 | Complete | 2026-03-14 |
| 13. Frontend Foundation | v2.0 | 3/3 | Complete | 2026-03-14 |
| 14. Dashboard & Provider Mgmt | v2.0 | 4/4 | Complete | 2026-03-14 |
| 15. Observability & Operations | v2.0 | 3/3 | Complete | 2026-03-14 |
| 16. Auth, Config & Production | v2.0 | 3/3 | Complete | 2026-03-14 |
| 17. Quick Wins | v3.0 | 3/3 | Complete | 2026-03-14 |
| 18. Circuit Breaker & Compaction | v3.0 | 3/3 | Complete | 2026-03-14 |
| 19. Saga Compensation | v3.0 | 2/2 | Complete | 2026-03-14 |
| 20. UI Enhancements | v3.0 | 3/3 | Complete | 2026-03-14 |

---
*Roadmap extended: 2026-03-14 -- v3.0 Infrastructure Maturity phases 17-20 added*
*Roadmap extended: 2026-03-14 -- v4.0 Log Streaming phases 21-22 added*

## v4.0 Log Streaming

**Milestone Goal:** Stream live stdout/stderr from running provider subprocesses and containers to browser clients over WebSocket. Providers currently crash silently -- engineers have no visibility into what a provider is doing. Fix the four critical gaps: live stderr reading (not post-mortem drain), Docker DEVNULL→PIPE, per-provider ring buffer for history, and a WebSocket endpoint + UI log viewer in the provider detail page.

### Phase 21: Log Capture Infrastructure

**Goal**: Per-provider log ring buffers exist, launchers stream stderr lines into them in real time, and a REST endpoint exposes log history -- establishing the capture layer before WebSocket streaming is added.
**Depends on**: Phase 20 (v3.0 complete)
**Requirements**: LOG-01, LOG-02, LOG-03
**Success Criteria** (what must be TRUE):

  1. `LogLine` value object (`provider_id`, `stream: Literal["stdout","stderr"]`, `content: str`, `recorded_at: float`) and `IProviderLogBuffer` interface (`append`, `tail`, `clear`, `provider_id`) exist in `domain/`. `ProviderLogBuffer` implements `IProviderLogBuffer` with a `collections.deque(maxlen=N)` ring buffer (default 1000 lines). Singleton registry `get_log_buffer(provider_id)` / `set_log_buffer(provider_id, buffer)` exists.
  2. `SubprocessLauncher` and `ContainerLauncher` spawn a daemon thread that reads `process.stderr` line-by-line and appends `LogLine(stream="stderr")` entries to the provider's buffer. `DockerLauncher` is changed from `stderr=subprocess.DEVNULL` to `stderr=subprocess.PIPE` and gains an identical reader thread. All reader threads terminate cleanly when the process exits.
  3. `GET /api/providers/{provider_id}/logs` accepts `lines` (default 100, max 1000) and returns `{"logs": [{...LogLine...}], "provider_id": "...", "count": N}`. Returns 404 for unknown providers, empty list for providers with no log buffer yet.

**Plans**: 3 plans

Plans:

- [x] 21-01-PLAN.md -- LogLine value object, IProviderLogBuffer interface, ProviderLogBuffer ring buffer, singleton registry (LOG-01)
- [x] 21-02-PLAN.md -- Live stderr-reader threads in SubprocessLauncher + ContainerLauncher + DockerLauncher (LOG-02)
- [x] 21-03-PLAN.md -- GET /api/providers/{id}/logs REST endpoint + unit tests (LOG-03)

### Phase 22: Log Streaming WebSocket + UI

**Goal**: Real-time log lines flow to browser clients over WebSocket, and the provider detail page shows a live log viewer with history, auto-scroll, and stream (stdout/stderr) coloring.
**Depends on**: Phase 21 (ring buffers and reader threads must be in place)
**Requirements**: LOG-04, LOG-05
**Success Criteria** (what must be TRUE):

  1. `LogStreamBroadcaster` has per-provider registered async callbacks. `IProviderLogBuffer.append()` notifies the broadcaster. A WebSocket endpoint `GET /api/ws/providers/{provider_id}/logs` sends the buffered history on connect (as individual `{"type":"log_line",...}` messages), then streams live lines until disconnect. Disconnection cleans up the registered callback (no leak).
  2. Bootstrap wires `LogStreamBroadcaster` and `ProviderLogBuffer` instances per configured provider. Integration test: connect WebSocket, trigger provider start, assert log lines arrive.
  3. `LogViewer` React component renders log lines in a monospace font with stderr in amber and stdout in gray. `useProviderLogs` hook manages the WebSocket connection with auto-reconnect. `ProviderDetailPage` has a "Process Logs" section at the bottom using `LogViewer`. `npx tsc --noEmit` exits 0.

**Plans**: 3 plans

Plans:

- [x] 22-01-PLAN.md -- LogStreamBroadcaster + GET /ws/providers/{id}/logs WebSocket endpoint (LOG-04)
- [x] 22-02-PLAN.md -- Bootstrap wiring (log buffer + broadcaster per provider) + integration tests (LOG-04)
- [x] 22-03-PLAN.md -- LogViewer component + useProviderLogs hook + ProviderDetailPage integration (LOG-05)

## v4.0 Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> ... -> 22

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Timing Attack Prevention | v0.9 | 2/2 | Complete | 2026-02-15 |
| 2. Rate Limiter Hardening | v0.9 | 2/2 | Complete | 2026-02-15 |
| 3. JWT Lifetime Enforcement | v0.9 | 1/1 | Complete | 2026-02-15 |
| 4. API Key Rotation | v0.9 | 2/2 | Complete | 2026-02-15 |
| 5. Documentation Content | v0.10 | 2/2 | Complete | 2026-02-28 |
| 6. Kubernetes Controllers | v0.10 | 3/3 | Complete | 2026-03-01 |
| 7. Helm Chart Maturity | v0.10 | 1/1 | Complete | 2026-03-01 |
| 8. Safety Foundation | v1.0 | 3/3 | Complete | 2026-03-08 |
| 9. State Survival | v1.0 | 3/3 | Complete | 2026-03-08 |
| 10. Operational Hardening | v1.0 | 6/6 | Complete | 2026-03-08 |
| 11. Backend REST API | v2.0 | 5/5 | Complete | 2026-03-14 |
| 12. WebSocket Infrastructure | v2.0 | 2/2 | Complete | 2026-03-14 |
| 13. Frontend Foundation | v2.0 | 3/3 | Complete | 2026-03-14 |
| 14. Dashboard & Provider Mgmt | v2.0 | 4/4 | Complete | 2026-03-14 |
| 15. Observability & Operations | v2.0 | 3/3 | Complete | 2026-03-14 |
| 16. Auth, Config & Production | v2.0 | 3/3 | Complete | 2026-03-14 |
| 17. Quick Wins | v3.0 | 3/3 | Complete | 2026-03-14 |
| 18. Circuit Breaker & Compaction | v3.0 | 3/3 | Complete | 2026-03-14 |
| 19. Saga Compensation | v3.0 | 2/2 | Complete | 2026-03-14 |
| 20. UI Enhancements | v3.0 | 3/3 | Complete | 2026-03-14 |
| 21. Log Capture Infrastructure | v4.0 | 3/3 | Complete | 2026-03-15 |
| 22. Log Streaming WebSocket + UI | v4.0 | 3/3 | Complete | 2026-03-15 |

---
*Roadmap extended: 2026-03-14 -- v3.0 Infrastructure Maturity phases 17-20 added*
*Roadmap extended: 2026-03-14 -- v4.0 Log Streaming phases 21-22 added*
*v4.0 Log Streaming COMPLETE: 2026-03-15 -- phases 21-22 shipped, LOG-01 through LOG-05 satisfied*
