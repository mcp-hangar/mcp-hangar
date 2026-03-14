# Roadmap: MCP Hangar

## Milestones

- ✅ **v0.9 Security Hardening** -- Phases 1-4 (shipped 2026-02-15)
- ✅ **v0.10 Documentation & Kubernetes Maturity** -- Phases 5-7 (shipped 2026-03-01)
- ✅ **v1.0 Production Hardening** -- Phases 8-10 (shipped 2026-03-08, released as v0.11.0: 2026-03-09)
- **v2.0 Management UI** -- Phases 11-16 (in progress)

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

### v2.0 Management UI (In Progress)

**Milestone Goal:** Add a browser-based management UI for MCP Hangar with full provider lifecycle control, real-time event streaming, metrics dashboards, auth management, and configuration visibility -- backed by a REST API layer wrapping existing CQRS infrastructure and WebSocket streaming from the EventBus.

- [x] **Phase 11: Backend REST API** - Starlette routes wrapping CQRS commands/queries for providers, groups, auth, config, discovery, events, metrics, audit, and system endpoints (completed 2026-03-14)
- [ ] **Phase 12: WebSocket Infrastructure** - Connection manager, EventBus subscription bridge (sync-to-async queue), event streaming channel, state snapshot channel
- [ ] **Phase 13: Frontend Foundation** - Vite + React + TypeScript project, routing, API client with TanStack Query, WebSocket hooks with auto-reconnect, layout shell, component library
- [ ] **Phase 14: Dashboard & Provider Management** - Dashboard page, provider list/detail, group management, discovery management, configuration viewer
- [ ] **Phase 15: Observability & Operations** - Metrics dashboards with Recharts, event stream viewer, audit log, execution history, security events
- [ ] **Phase 16: Auth, Config & Production** - API key management, role management, production static build serving, SPA routing fallback, Docker multi-stage build

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
**Plans**: TBD

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
**Plans**: TBD

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
**Plans**: TBD

### Phase 15: Observability & Operations

**Goal**: Operational visibility pages are functional -- metrics dashboards with charts, event stream viewer, audit log, execution history, and security events
**Depends on**: Phase 14 (dashboard patterns, chart components, and table components established)
**Requirements**: UI-09, UI-10, UI-11, UI-12
**Success Criteria** (what must be TRUE):

  1. Metrics page shows RED metrics per provider with Recharts visualizations and SLI availability/error budget
  2. Events page shows live WebSocket event stream with type/severity filters and paginated audit log with entity/time filters
  3. Executions page shows tool invocation timeline, failures view with error details, and success rate/p95 statistics
  4. Security events viewer shows security events with severity indicators
**Plans**: TBD

### Phase 16: Auth, Config & Production

**Goal**: Auth management UI is functional and the application is production-ready -- API key management, role management, static build serving with SPA fallback, and Docker multi-stage build
**Depends on**: Phase 15 (all feature pages complete, patterns established)
**Requirements**: UI-15, UI-16, INTG-02, INTG-04
**Success Criteria** (what must be TRUE):

  1. Auth page provides API key management (list, create, revoke) per principal
  2. Auth page provides role management (list builtin/custom, create custom) and role assignment (assign/revoke)
  3. Backend serves UI static build in production mode with SPA routing fallback for all non-API routes
  4. Multi-stage Docker build produces a single image with Python backend and UI static files
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 11 -> 12 -> 13 -> 14 -> 15 -> 16

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
| 11. Backend REST API | v2.0 | Complete    | 2026-03-14 | 2026-03-14 |
| 12. WebSocket Infrastructure | v2.0 | 0/? | Pending | -- |
| 13. Frontend Foundation | v2.0 | 0/? | Pending | -- |
| 14. Dashboard & Provider Mgmt | v2.0 | 0/? | Pending | -- |
| 15. Observability & Operations | v2.0 | 0/? | Pending | -- |
| 16. Auth, Config & Production | v2.0 | 0/? | Pending | -- |

---
*Created: 2026-02-15*
*Last updated: 2026-03-14 -- Phase 11-05 complete: observability REST endpoints (metrics, audit, security, alerts) -- Phase 11 Backend REST API COMPLETE*
