# Requirements: MCP Hangar

**Defined:** 2026-03-08
**Core Value:** Reliable, observable MCP provider management with production-grade lifecycle control -- providers start, run, degrade, and recover predictably with full audit trail.

## v1.0 Requirements (Complete)

All 17 v1.0 requirements satisfied. Archived in `.planning/milestones/v1.0-REQUIREMENTS.md`.

Summary: CONC-01..04 (concurrency safety), EXCP-01 (exception hygiene), SECR-01..02 (security), PERS-01..05 (state persistence), RESL-01..03 (resilience), TEST-01 (property-based testing), QUAL-01 (typing strictness).

## v2.0 Requirements

Requirements for v2.0 Management UI. The goal is a browser-based management interface for MCP Hangar with full provider lifecycle control, real-time observability, and configuration management.

### Backend REST API

- [x] **REST-01**: REST API endpoints exist for all provider lifecycle operations (list, get, start, stop) wrapping existing CQRS commands/queries, returning JSON responses with consistent error format
- [ ] **REST-02**: REST API endpoints exist for provider group operations (list groups, get group detail, trigger rebalance) wrapping existing CQRS and saga infrastructure
- [x] **REST-03**: REST API endpoints exist for tool listing per provider (including JSON Schema) and tool invocation history queried from the event store
- [x] **REST-04**: REST API endpoints exist for authentication management (create/revoke API keys, create/list roles, assign/revoke role assignments) wrapping existing auth CQRS handlers
- [ ] **REST-05**: REST API endpoints exist for configuration (get current config, trigger hot reload) and system info (build info, uptime, provider counts)
- [ ] **REST-06**: REST API endpoints exist for discovery management (list sources with health, list pending providers, list quarantined providers, approve/reject discovered providers)
- [x] **REST-07**: REST API endpoints exist for observability data (metrics as JSON, audit log with entity/time filters, security events, alert history)
- [x] **REST-08**: All REST handlers use `starlette.concurrency.run_in_threadpool()` for CQRS dispatch -- no sync operations block the ASGI event loop
- [x] **REST-09**: REST API is mounted under `/api/` prefix on the existing ASGI application alongside existing routes (`/health`, `/ready`, `/metrics`, `/mcp`), with no disruption to existing endpoints
- [x] **REST-10**: REST error responses use a consistent JSON envelope (`{"error": {"code": ..., "message": ..., "details": ...}}`) mapping domain exceptions to appropriate HTTP status codes

### WebSocket Streaming

- [ ] **WS-01**: A WebSocket endpoint at `/api/ws/events` streams domain events in real-time by subscribing to `EventBus.subscribe_to_all()`, with client-side subscription filters (event type, provider ID, severity)
- [ ] **WS-02**: A WebSocket endpoint at `/api/ws/state` sends periodic provider/group state snapshots at a configurable interval (default 2s)
- [ ] **WS-03**: WebSocket connection manager tracks active connections, handles clean disconnection (unsubscribe from EventBus), and detects dead connections via ping/pong heartbeat
- [ ] **WS-04**: EventBus has an `unsubscribe_from_all(handler)` method to support WebSocket connection cleanup without handler/memory leaks
- [ ] **WS-05**: A thread-safe queue bridges sync EventBus handlers to async WebSocket broadcast -- events are queued synchronously and broadcast asynchronously

### Frontend Foundation

- [ ] **UI-01**: A React + TypeScript + Vite project exists in `packages/ui/` with client-side routing (react-router), API client layer (TanStack Query), and WebSocket hooks with auto-reconnect
- [ ] **UI-02**: The UI has a consistent layout shell (sidebar navigation, header with system status, content area) built with Tailwind CSS and Radix UI primitives
- [ ] **UI-03**: The API client layer provides typed functions for all REST endpoints with TanStack Query integration (caching, background refetch, optimistic updates for mutations)
- [ ] **UI-04**: WebSocket events trigger TanStack Query cache invalidation (not direct state replacement) to maintain consistency between REST and real-time data

### Dashboard & Provider Management

- [ ] **UI-05**: A dashboard page shows at-a-glance system health: provider state distribution chart, key metric cards (total providers, active tools, invocations, error rate), recent events feed (live via WebSocket), and alert summary
- [ ] **UI-06**: A providers page shows a filterable/sortable table of all providers with state indicators, mode, tools count, health status, and start/stop action buttons
- [ ] **UI-07**: A provider detail view shows full provider info (state, health history, tool list with schemas, circuit breaker state, event timeline from event store)
- [ ] **UI-08**: A groups page shows group list with strategy, member count, healthy count, and circuit breaker status; group detail view shows member list with individual states and rebalance action

### Observability & Operations

- [ ] **UI-09**: A metrics page shows RED metrics (rate, errors, duration) per provider with Recharts visualizations, plus SLI availability ratio and error budget
- [ ] **UI-10**: An events page shows a live WebSocket-fed event stream with type/severity filters, plus a paginated audit log view with entity and time range filters
- [ ] **UI-11**: An executions page shows tool invocation history (timeline, failures filtered view with error details, success rate and p95 latency statistics)
- [ ] **UI-12**: A security events viewer shows security-specific events with severity indicators, sourced from `SecurityHandler.query()`

### Discovery & Configuration

- [ ] **UI-13**: A discovery page shows discovery sources with health and last scan time, pending providers with approve/reject actions, and quarantined providers with reasons
- [ ] **UI-14**: A configuration page shows current active configuration in read-only view, environment variables, and a hot reload trigger button with result display

### Auth & Security Management

- [ ] **UI-15**: An auth page provides API key management (list, create, revoke) per principal
- [ ] **UI-16**: An auth page provides role management (list builtin/custom roles, create custom roles) and role assignment management (assign/revoke roles for principals)

### Integration & Deployment

- [x] **INTG-01**: CORS middleware is configured on the ASGI app with configurable allowed origins (development: `localhost:5173`, production: same-origin or configured domain)
- [ ] **INTG-02**: In production mode, the backend serves the UI static build (`vite build` output) with SPA routing fallback -- all non-API/non-system routes return `index.html`
- [ ] **INTG-03**: Vite dev server proxies `/api/*` requests to the backend, enabling frontend development without CORS issues
- [ ] **INTG-04**: Multi-stage Docker build produces a single image containing both Python backend and UI static files

## Deferred (from v1.0)

Carried forward from v1.0. Not in v2.0 scope unless explicitly promoted.

### Exception Hygiene

- **EXCP-02**: Ruff BLE001 bare-except lint rule enabled project-wide

### State Persistence

- **PERS-06**: Saga compensation for automatic step undo on partial failure
- **PERS-07**: Circuit breaker HALF_OPEN state for controlled probe requests
- **PERS-08**: Snapshot compaction to prune old events after snapshot creation

### Resilience & Observability

- **RESL-04**: Prometheus metrics for rate limit hits, rejections, and bucket utilization

### Testing

- **TEST-02**: Fuzz testing for event deserialization

## Out of Scope

Explicitly excluded from v2.0. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Multi-tenant UI | Tenant isolation adds complexity beyond management UI scope |
| Kubernetes operator UI | Operator has its own CRD-based management |
| Provider code editor | MCP Hangar manages providers, doesn't develop them |
| Grafana embedding | Use Grafana directly for advanced dashboards |
| Mobile responsive design | Management UI is desktop-focused |
| i18n/localization | English-only for v2.0 |
| Provider topology visualization (D3) | Nice-to-have, not table-stakes. Can add in v2.1 |
| Config editor with YAML validation | High complexity. Read-only config view is sufficient for v2.0 |
| Alert rules configuration UI | Currently hardcoded thresholds. Config-driven alerting is a separate concern |
| Log streaming from providers | Needs ring buffer log handler infrastructure not yet built |
| Metric time-series persistence | Currently in-memory, reset on restart. Requires storage backend for history |
| Distributed saga coordination | Single-process by design |
| Async/asyncio rewrite of domain layer | Thread-based by design per CLAUDE.md |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| REST-01 | Phase 11 | Complete |
| REST-02 | Phase 11 | Pending |
| REST-03 | Phase 11 | Complete |
| REST-04 | Phase 11 | Complete |
| REST-05 | Phase 11 | Pending |
| REST-06 | Phase 11 | Pending |
| REST-07 | Phase 11 | Complete |
| REST-08 | Phase 11 | Complete |
| REST-09 | Phase 11 | Complete |
| REST-10 | Phase 11 | Complete |
| WS-01 | Phase 12 | Pending |
| WS-02 | Phase 12 | Pending |
| WS-03 | Phase 12 | Pending |
| WS-04 | Phase 12 | Pending |
| WS-05 | Phase 12 | Pending |
| UI-01 | Phase 13 | Pending |
| UI-02 | Phase 13 | Pending |
| UI-03 | Phase 13 | Pending |
| UI-04 | Phase 13 | Pending |
| UI-05 | Phase 14 | Pending |
| UI-06 | Phase 14 | Pending |
| UI-07 | Phase 14 | Pending |
| UI-08 | Phase 14 | Pending |
| UI-09 | Phase 15 | Pending |
| UI-10 | Phase 15 | Pending |
| UI-11 | Phase 15 | Pending |
| UI-12 | Phase 15 | Pending |
| UI-13 | Phase 14 | Pending |
| UI-14 | Phase 14 | Pending |
| UI-15 | Phase 16 | Pending |
| UI-16 | Phase 16 | Pending |
| INTG-01 | Phase 11 | Complete |
| INTG-02 | Phase 16 | Pending |
| INTG-03 | Phase 13 | Pending |
| INTG-04 | Phase 16 | Pending |

**Coverage:**

- v2.0 requirements: 36 total (10 REST + 5 WS + 16 UI + 4 INTG + 1 deferred EXCP + 3 deferred PERS + 1 deferred RESL + 1 deferred TEST = 36 new + 6 deferred)
- Mapped to phases: 36
- Unmapped: 0

---
*Requirements defined: 2026-03-08*
*Last updated: 2026-03-14 -- v2.0 Management UI requirements added*
