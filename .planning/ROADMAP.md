# Roadmap: MCP Hangar

## Milestones

- ✅ **v0.9 Security Hardening** -- Phases 1-4 (shipped 2026-02-15)
- ✅ **v0.10 Documentation & Kubernetes Maturity** -- Phases 5-7 (shipped 2026-03-01)
- ✅ **v1.0 Production Hardening** -- Phases 8-10 (shipped 2026-03-08, released as v0.11.0: 2026-03-09)
- ✅ **v2.0 Management UI** -- Phases 11-16 (shipped 2026-03-14)
- ✅ **v3.0 Infrastructure Maturity** -- Phases 17-20 (shipped 2026-03-14)
- ✅ **v4.0 Log Streaming** -- Phases 21-22 (shipped 2026-03-15)
- 🔨 **v5.0 Platform Management Console** -- Phases 23-30 (in progress)

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

<details>
<summary>✅ v2.0 Management UI Phase Details (Phases 11-16) -- SHIPPED 2026-03-14</summary>

### Phase 11: Backend REST API

**Goal**: A complete REST API layer exists under `/api/` wrapping all CQRS commands and queries -- provider lifecycle, groups, auth, config, discovery, events, metrics, audit, system.

- [x] 11-01-PLAN.md -- API foundation + provider endpoints (REST-01, REST-03, REST-08..10, INTG-01) -- completed 2026-03-14
- [x] 11-02-PLAN.md -- Groups, discovery, config, system endpoints (REST-02, REST-04..07) -- completed 2026-03-14
- [x] 11-03-PLAN.md -- Tool invocation history endpoint + thread-safety fix (REST-03 gap) -- completed 2026-03-14
- [x] 11-04-PLAN.md -- Auth management endpoints: API keys and roles (REST-04 gap) -- completed 2026-03-14
- [x] 11-05-PLAN.md -- Observability endpoints: metrics, audit, security, alerts (REST-07 gap) -- completed 2026-03-14

### Phase 12: WebSocket Infrastructure

**Goal**: Real-time event streaming and state updates over WebSocket, with clean connection lifecycle management and no resource leaks.

- [x] 12-01-PLAN.md -- EventBus.unsubscribe_from_all + WebSocket infrastructure (WS-03..05)
- [x] 12-02-PLAN.md -- WebSocket endpoints (events, state) + ASGI routing (WS-01..03, WS-05)

### Phase 13: Frontend Foundation

**Goal**: React + TypeScript project with routing, typed API client, WebSocket hooks, and layout shell.

- [x] 13-01-PLAN.md -- Vite + React + TypeScript scaffold, types, API client, TanStack Query keys (UI-01..02, INTG-03)
- [x] 13-02-PLAN.md -- WebSocket hooks: useWebSocket, useEventStream, useProviderState + Zustand WS store (UI-03, INTG-03)
- [x] 13-03-PLAN.md -- Layout shell, routing (all 9 pages), sidebar nav, header with system status (UI-01, UI-04)

### Phase 14: Dashboard & Provider Management

**Goal**: Core management pages -- dashboard, provider list/detail, group management, discovery, config viewer.

- [x] 14-01-PLAN.md -- Shared UI primitives: ProviderStateBadge, HealthBadge, CircuitBreakerBadge, MetricCard, etc.
- [x] 14-02-PLAN.md -- Dashboard page: metric cards, state distribution chart, live event feed, alert summary (UI-05)
- [x] 14-03-PLAN.md -- Provider list (filtered table + start/stop) + Provider detail (UI-06)
- [x] 14-04-PLAN.md -- Groups + discovery + config viewer (UI-07..08, UI-13)

### Phase 15: Observability & Operations

**Goal**: Metrics dashboards, event stream viewer, audit log, execution history, security events.

- [x] 15-01-PLAN.md -- API client fixes + MetricsPage (UI-09)
- [x] 15-02-PLAN.md -- EventsPage: live stream + paginated audit log (UI-10)
- [x] 15-03-PLAN.md -- ExecutionsPage + SecurityPage (UI-11..12)

### Phase 16: Auth, Config & Production

**Goal**: Auth management UI, static build serving with SPA fallback, Docker multi-stage build.

- [x] 16-01-PLAN.md -- AuthPage: API key management + role management (UI-15..16) -- completed 2026-03-14
- [x] 16-02-PLAN.md -- Static build serving + SPA fallback (INTG-02) -- completed 2026-03-14
- [x] 16-03-PLAN.md -- Multi-stage Docker build with UI baked in (INTG-04) -- completed 2026-03-14

</details>

<details>
<summary>✅ v3.0 Infrastructure Maturity Phase Details (Phases 17-20) -- SHIPPED 2026-03-14</summary>

**Milestone Goal:** Close all six requirements deferred from v1.0 (EXCP-02, PERS-06, PERS-07, PERS-08, RESL-04, TEST-02), extend observability depth, implement snapshot compaction, harden circuit breaker with HALF_OPEN probe semantics, put saga compensation into production use, and promote two UI features from the v2.0 out-of-scope list.

### Phase 17: Quick Wins

- [x] 17-01-PLAN.md -- BLE001 enablement (EXCP-02) -- completed 2026-03-14
- [x] 17-02-PLAN.md -- Rate limit metrics: result label + ACTIVE_BUCKETS gauge (RESL-04) -- completed 2026-03-14
- [x] 17-03-PLAN.md -- Fuzz tests: 4 @given tests for EventSerializer + UpcasterChain (TEST-02) -- completed 2026-03-14

### Phase 18: Circuit Breaker & Event Store Compaction

- [x] 18-01-PLAN.md -- CircuitBreaker HALF_OPEN state machine (PERS-07) -- completed 2026-03-14
- [x] 18-02-PLAN.md -- Circuit breaker instrumentation: CircuitBreakerStateChanged event, Gauge (PERS-07) -- completed 2026-03-14
- [x] 18-03-PLAN.md -- Snapshot compaction: compact_stream(), admin endpoint, counter (PERS-08) -- completed 2026-03-14

### Phase 19: Saga Compensation

- [x] 19-01-PLAN.md -- SagaManager delayed-command facility + ProviderFailoverSaga step conversion (PERS-06) -- completed 2026-03-14
- [x] 19-02-PLAN.md -- Saga compensation integration tests: happy path + COMPENSATING->COMPENSATED (PERS-06) -- completed 2026-03-14

### Phase 20: UI Enhancements

- [x] 20-01-PLAN.md -- MetricsHistoryStore (SQLite) + GET /api/metrics/history endpoint (UI-18 backend) -- completed 2026-03-14
- [x] 20-02-PLAN.md -- MetricsPage time-series: time-range selector, line chart (UI-18 frontend) -- completed 2026-03-14
- [x] 20-03-PLAN.md -- Topology page: D3.js force-directed graph, state-colored nodes, WebSocket updates (UI-17) -- completed 2026-03-14

</details>

<details>
<summary>✅ v4.0 Log Streaming Phase Details (Phases 21-22) -- SHIPPED 2026-03-15</summary>

**Milestone Goal:** Stream live stdout/stderr from provider subprocesses and Docker containers to browser clients over WebSocket. Fix four critical gaps: live stderr reading (not post-mortem drain), Docker DEVNULL-to-PIPE, per-provider ring buffer, and a WebSocket endpoint + UI log viewer.

### Phase 21: Log Capture Infrastructure

- [x] 21-01-PLAN.md -- LogLine value object, IProviderLogBuffer interface, ProviderLogBuffer ring buffer, singleton registry (LOG-01) -- completed 2026-03-15
- [x] 21-02-PLAN.md -- Live stderr-reader threads in SubprocessLauncher + DockerLauncher, DEVNULL-to-PIPE (LOG-02) -- completed 2026-03-15
- [x] 21-03-PLAN.md -- GET /api/providers/{id}/logs REST endpoint + unit tests (LOG-03) -- completed 2026-03-15

### Phase 22: Log Streaming WebSocket + UI

- [x] 22-01-PLAN.md -- LogStreamBroadcaster + GET /api/ws/providers/{id}/logs WebSocket endpoint (LOG-04) -- completed 2026-03-15
- [x] 22-02-PLAN.md -- Bootstrap wiring (log buffer + broadcaster per provider) + integration tests (LOG-04) -- completed 2026-03-15
- [x] 22-03-PLAN.md -- LogViewer component + useProviderLogs hook + ProviderDetailPage integration (LOG-05) -- completed 2026-03-15

</details>

<details>
<summary>🔨 v5.0 Platform Management Console Phase Details (Phases 23-30) -- IN PROGRESS</summary>

**Milestone Goal:** Transform MCP Hangar from a config-file-driven system into a full platform management console. CRUD for providers/groups, discovery source management, MCP catalog, RBAC management, tool access policy editing, and config export/backup/diff -- from UI, API, and static config. Two parallel tracks: Core (Python) and UI (React/TypeScript).

### Phase 23: Provider/Group CRUD + Config Serialization (C1)

- [ ] 23-01-PLAN.md -- New domain events (ProviderRegistered with source field, ProviderUpdated, ProviderDeregistered, GroupUpdated, GroupDeleted) + provider CQRS commands + handlers (CRUD-01)
- [ ] 23-02-PLAN.md -- Group CQRS commands + handlers (CRUD-02)
- [ ] 23-03-PLAN.md -- Config serializer: serialize functions + backup rotation (CRUD-03)
- [ ] 23-04-PLAN.md -- REST endpoints: provider/group CRUD + config export/backup (CRUD-01, CRUD-02, CRUD-03)
- [ ] 23-05-PLAN.md -- Unit + integration tests (CRUD-01, CRUD-02, CRUD-03)

### Phase 24: Discovery Source Management + Static MCP Catalog API (C2)

- [ ] 24-01-PLAN.md -- DiscoveryRegistry + DiscoverySourceSpec (DISC-01)
- [ ] 24-02-PLAN.md -- Discovery REST endpoints (DISC-02)
- [ ] 24-03-PLAN.md -- McpProviderEntry model + McpCatalogRepository + seed (CAT-01)
- [ ] 24-04-PLAN.md -- Catalog REST endpoints + deploy action (CAT-02)
- [ ] 24-05-PLAN.md -- Discovery + catalog tests (DISC-01, DISC-02, CAT-01, CAT-02)

### Phase 25: Provider & Group CRUD Forms (U1)

- [ ] 25-01-PLAN.md -- TypeScript types + MSW handlers + API client for CRUD (CRUD-04)
- [ ] 25-02-PLAN.md -- ProviderCreateDrawer + ProviderEditDrawer (CRUD-04)
- [ ] 25-03-PLAN.md -- ProviderDeleteDialog + ProvidersPage actions (CRUD-04)
- [ ] 25-04-PLAN.md -- GroupCreateDrawer + GroupEditDrawer + GroupMemberPanel (CRUD-05)
- [ ] 25-05-PLAN.md -- ToolAccessPolicyEditor + GroupsPage enhancements (CRUD-05)

### Phase 26: Discovery Source Wizard + Catalog Browser (U2)

- [ ] 26-01-PLAN.md -- TypeScript types + MSW handlers for discovery/catalog (DISC-03, CAT-03)
- [ ] 26-02-PLAN.md -- AddDiscoverySourceWizard + DiscoveryPage enhancements (DISC-03)
- [ ] 26-03-PLAN.md -- CatalogPage: search, tags, grid, Deploy + CatalogEntryDrawer (CAT-03)
- [ ] 26-04-PLAN.md -- Sidebar Catalog link + component tests (DISC-03, CAT-03)

### Phase 27: RBAC Management API + Tool Access Policy CRUD (C3)

- [ ] 27-01-PLAN.md -- RoleStore (SQLite) + custom role CRUD + events (RBAC-01)
- [ ] 27-02-PLAN.md -- REST endpoints: roles, principals, permissions (RBAC-02)
- [ ] 27-03-PLAN.md -- Tool access policy REST endpoints (TAP-01)
- [ ] 27-04-PLAN.md -- Bootstrap wiring for RoleStore + access endpoints (RBAC-01, RBAC-02, TAP-01)
- [ ] 27-05-PLAN.md -- Tests: RoleStore, permissions, access policy (RBAC-01, RBAC-02, TAP-01)

### Phase 28: RBAC Management + Tool Access Policy Editor (U3)

- [ ] 28-01-PLAN.md -- TypeScript types + MSW handlers for roles/principals/access (RBAC-03, TAP-02)
- [ ] 28-02-PLAN.md -- SecurityPage tabs: Events | Roles | Principals; Create Role drawer (RBAC-03)
- [ ] 28-03-PLAN.md -- Principals tab: role assignment, effective permissions (RBAC-03)
- [ ] 28-04-PLAN.md -- ToolAccessPolicyEditor in ProviderDetail + GroupsPage + tests (TAP-02)

### Phase 29: Config Export Finalization + Persistence Integration (C4)

- [ ] 29-01-PLAN.md -- Extended serialize_full_config with all sections (CFG-01)
- [ ] 29-02-PLAN.md -- GET /api/config/diff endpoint (CFG-01)
- [ ] 29-03-PLAN.md -- Full backup snapshot + rotation validation (CFG-01)
- [ ] 29-04-PLAN.md -- Round-trip integration tests (CFG-03)

### Phase 30: Config Export UI + Integration Polish (U4)

- [ ] 30-01-PLAN.md -- TypeScript types + MSW handlers for config export/diff (CFG-02)
- [ ] 30-02-PLAN.md -- ConfigPage tabs: Current Config | Export/Backup | Diff (CFG-02)
- [ ] 30-03-PLAN.md -- Diff tab: colored diff view (CFG-02)
- [ ] 30-04-PLAN.md -- Radix Toast notification system (CFG-02, XC-02)
- [ ] 30-05-PLAN.md -- Integration polish: skeletons, validation, MSW completeness, test coverage (XC-01, XC-02)

</details>

## Progress

**22 of 30 phases complete across 7 milestones. v5.0 in planning.**

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
| 23. Provider/Group CRUD (C1) | 1/5 | In Progress|  | -- |
| 24. Discovery + Catalog API (C2) | v5.0 | 0/5 | Planning | -- |
| 25. CRUD Forms (U1) | v5.0 | 0/5 | Planning | -- |
| 26. Discovery Wizard + Catalog (U2) | v5.0 | 0/4 | Planning | -- |
| 27. RBAC + Tool Access API (C3) | v5.0 | 0/5 | Planning | -- |
| 28. RBAC + Tool Access UI (U3) | v5.0 | 0/4 | Planning | -- |
| 29. Config Export (C4) | v5.0 | 0/4 | Planning | -- |
| 30. Config Export UI + Polish (U4) | v5.0 | 0/5 | Planning | -- |

---
*Roadmap extended: 2026-03-14 -- v3.0 Infrastructure Maturity phases 17-20 added*
*Roadmap extended: 2026-03-14 -- v4.0 Log Streaming phases 21-22 added*
*v4.0 Log Streaming COMPLETE: 2026-03-15 -- phases 21-22 shipped, LOG-01 through LOG-05 satisfied*
*Roadmap extended: 2026-03-22 -- v5.0 Platform Management Console phases 23-30 added (8 phases, 37 plans, 2 tracks)*
