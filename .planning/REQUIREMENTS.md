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
- [x] **REST-02**: REST API endpoints exist for provider group operations (list groups, get group detail, trigger rebalance) wrapping existing CQRS and saga infrastructure
- [x] **REST-03**: REST API endpoints exist for tool listing per provider (including JSON Schema) and tool invocation history queried from the event store
- [x] **REST-04**: REST API endpoints exist for authentication management (create/revoke API keys, create/list roles, assign/revoke role assignments) wrapping existing auth CQRS handlers
- [x] **REST-05**: REST API endpoints exist for configuration (get current config, trigger hot reload) and system info (build info, uptime, provider counts)
- [x] **REST-06**: REST API endpoints exist for discovery management (list sources with health, list pending providers, list quarantined providers, approve/reject discovered providers)
- [x] **REST-07**: REST API endpoints exist for observability data (metrics as JSON, audit log with entity/time filters, security events, alert history)
- [x] **REST-08**: All REST handlers use `starlette.concurrency.run_in_threadpool()` for CQRS dispatch -- no sync operations block the ASGI event loop
- [x] **REST-09**: REST API is mounted under `/api/` prefix on the existing ASGI application alongside existing routes (`/health`, `/ready`, `/metrics`, `/mcp`), with no disruption to existing endpoints
- [x] **REST-10**: REST error responses use a consistent JSON envelope (`{"error": {"code": ..., "message": ..., "details": ...}}`) mapping domain exceptions to appropriate HTTP status codes

### WebSocket Streaming

- [x] **WS-01**: A WebSocket endpoint at `/api/ws/events` streams domain events in real-time by subscribing to `EventBus.subscribe_to_all()`, with client-side subscription filters (event type, provider ID, severity)
- [x] **WS-02**: A WebSocket endpoint at `/api/ws/state` sends periodic provider/group state snapshots at a configurable interval (default 2s)
- [x] **WS-03**: WebSocket connection manager tracks active connections, handles clean disconnection (unsubscribe from EventBus), and detects dead connections via ping/pong heartbeat
- [x] **WS-04**: EventBus has an `unsubscribe_from_all(handler)` method to support WebSocket connection cleanup without handler/memory leaks
- [x] **WS-05**: A thread-safe queue bridges sync EventBus handlers to async WebSocket broadcast -- events are queued synchronously and broadcast asynchronously

### Frontend Foundation

- [x] **UI-01**: A React + TypeScript + Vite project exists in `packages/ui/` with client-side routing (react-router), API client layer (TanStack Query), and WebSocket hooks with auto-reconnect
- [x] **UI-02**: The UI has a consistent layout shell (sidebar navigation, header with system status, content area) built with Tailwind CSS and Radix UI primitives
- [x] **UI-03**: The API client layer provides typed functions for all REST endpoints with TanStack Query integration (caching, background refetch, optimistic updates for mutations)
- [x] **UI-04**: WebSocket events trigger TanStack Query cache invalidation (not direct state replacement) to maintain consistency between REST and real-time data

### Dashboard & Provider Management

- [x] **UI-05**: A dashboard page shows at-a-glance system health: provider state distribution chart, key metric cards (total providers, active tools, invocations, error rate), recent events feed (live via WebSocket), and alert summary
- [x] **UI-06**: A providers page shows a filterable/sortable table of all providers with state indicators, mode, tools count, health status, and start/stop action buttons
- [x] **UI-07**: A provider detail view shows full provider info (state, health history, tool list with schemas, circuit breaker state, event timeline from event store)
- [x] **UI-08**: A groups page shows group list with strategy, member count, healthy count, and circuit breaker status; group detail view shows member list with individual states and rebalance action

### Observability & Operations

- [x] **UI-09**: A metrics page shows RED metrics (rate, errors, duration) per provider with Recharts visualizations, plus SLI availability ratio and error budget
- [x] **UI-10**: An events page shows a live WebSocket-fed event stream with type/severity filters, plus a paginated audit log view with entity and time range filters
- [x] **UI-11**: An executions page shows tool invocation history (timeline, failures filtered view with error details, success rate and p95 latency statistics)
- [x] **UI-12**: A security events viewer shows security-specific events with severity indicators, sourced from `SecurityHandler.query()`

### Discovery & Configuration

- [x] **UI-13**: A discovery page shows discovery sources with health and last scan time, pending providers with approve/reject actions, and quarantined providers with reasons
- [x] **UI-14**: A configuration page shows current active configuration in read-only view, environment variables, and a hot reload trigger button with result display

### Auth & Security Management

- [x] **UI-15**: An auth page provides API key management (list, create, revoke) per principal
- [x] **UI-16**: An auth page provides role management (list builtin/custom roles, create custom roles) and role assignment management (assign/revoke roles for principals)

### Integration & Deployment

- [x] **INTG-01**: CORS middleware is configured on the ASGI app with configurable allowed origins (development: `localhost:5173`, production: same-origin or configured domain)
- [x] **INTG-02**: In production mode, the backend serves the UI static build (`vite build` output) with SPA routing fallback -- all non-API/non-system routes return `index.html`
- [x] **INTG-03**: Vite dev server proxies `/api/*` requests to the backend, enabling frontend development without CORS issues
- [x] **INTG-04**: Multi-stage Docker build produces a single image containing both Python backend and UI static files

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
| REST-02 | Phase 11 | Complete |
| REST-03 | Phase 11 | Complete |
| REST-04 | Phase 11 | Complete |
| REST-05 | Phase 11 | Complete |
| REST-06 | Phase 11 | Complete |
| REST-07 | Phase 11 | Complete |
| REST-08 | Phase 11 | Complete |
| REST-09 | Phase 11 | Complete |
| REST-10 | Phase 11 | Complete |
| WS-01 | Phase 12 | Complete |
| WS-02 | Phase 12 | Complete |
| WS-03 | Phase 12 | Complete |
| WS-04 | Phase 12 | Complete |
| WS-05 | Phase 12 | Complete |
| UI-01 | Phase 13 | Complete |
| UI-02 | Phase 13 | Complete |
| UI-03 | Phase 13 | Complete |
| UI-04 | Phase 13 | Complete |
| UI-05 | Phase 14 | Complete |
| UI-06 | Phase 14 | Complete |
| UI-07 | Phase 14 | Complete |
| UI-08 | Phase 14 | Complete |
| UI-09 | Phase 15 | Complete |
| UI-10 | Phase 15 | Complete |
| UI-11 | Phase 15 | Complete |
| UI-12 | Phase 15 | Complete |
| UI-13 | Phase 14 | Complete |
| UI-14 | Phase 14 | Complete |
| UI-15 | Phase 16 | Complete |
| UI-16 | Phase 16 | Complete |
| INTG-01 | Phase 11 | Complete |
| INTG-02 | Phase 16 | Complete |
| INTG-03 | Phase 13 | Complete |
| INTG-04 | Phase 16 | Complete |

**Coverage:**

- v2.0 requirements: 36 total (10 REST + 5 WS + 16 UI + 4 INTG + 1 deferred EXCP + 3 deferred PERS + 1 deferred RESL + 1 deferred TEST = 36 new + 6 deferred)
- Mapped to phases: 36
- Unmapped: 0

---
*Requirements defined: 2026-03-08*
*Last updated: 2026-03-14 -- v2.0 Management UI COMPLETE: all 35 requirements satisfied*

## v3.0 Requirements

Requirements for v3.0 Infrastructure Maturity. The goal is to close all six requirements deferred from v1.0, extend observability depth, promote two formerly out-of-scope UI features, and lay the correctness foundation for the saga and event store to carry the system into multi-provider production use at scale.

### Exception Hygiene

- [x] **EXCP-02**: Ruff BLE001 bare-except lint rule is enabled project-wide. All ~100 fault-barrier `except Exception` sites carry `# noqa: BLE001` with a one-line inline justification comment. The four REST API JSON-parsing catches (`server/api/providers.py`, `auth.py`, `system.py`, `config.py`) are narrowed to `json.JSONDecodeError | ValueError`. No new `except Exception` without either a noqa or an immediate re-raise to a typed exception.

### State Persistence & Sagas

- [x] **PERS-06**: At least one production saga uses the step-based `Saga` class with explicit compensation commands. `ProviderFailoverSaga` is converted from `EventTriggeredSaga` to a `Saga` subclass with named steps and per-step `compensation_command` fields. `SagaManager` gains a delayed-command facility (replacing the inline TODO scheduler comments in `ProviderRecoverySaga` and `ProviderFailoverSaga`) so backoff delays and failback delays are enforced rather than skipped. The compensation path (`COMPENSATING` → `COMPENSATED`) is exercised by integration tests covering a mid-saga failure.

- [x] **PERS-07**: `CircuitBreaker` implements the full three-state machine: `CLOSED`, `OPEN`, `HALF_OPEN`. In `OPEN` state, `allow_request()` does not silently reset to `CLOSED` on timeout expiry; instead it transitions to `HALF_OPEN` and allows exactly `probe_count` (default: 1) requests through. A probe success transitions to `CLOSED`; a probe failure resets to `OPEN` with a fresh `opened_at`. `CircuitBreakerConfig` gains `probe_count: int = 1`. `from_dict()` handles old snapshots that lack `HALF_OPEN` without error (backward compat). `mcp_hangar_circuit_breaker_state` Prometheus Gauge (labels: `provider`, `state`) is implemented and updated on every state transition. A `CircuitBreakerStateChanged` domain event is emitted on every transition.

- [x] **PERS-08**: `IEventStore` gains a `compact_stream(stream_id: str) -> int` method that deletes all events whose `stream_version` is less than or equal to the latest snapshot version for that stream, returning the count of deleted events. Both `SQLiteEventStore` and `InMemoryEventStore` implement it. Compaction is refused (raises `CompactionError`) when no snapshot exists for the stream. An admin endpoint `POST /api/maintenance/compact` accepts `{"stream_id": "..."}` and returns `{"deleted_events": N}`. A Prometheus counter `mcp_hangar_events_compacted_total` is incremented by N on each successful compaction.

### Resilience & Observability

- [x] **RESL-04**: Prometheus metrics for rate limiting are complete. `RATE_LIMIT_HITS_TOTAL` counter (label: `endpoint`, `result`: `allowed`|`rejected`) replaces the current rejections-only counter, so rejection rate is computable as `rejected / (allowed + rejected)`. A `RATE_LIMIT_ACTIVE_BUCKETS` Gauge tracks the live bucket count from `InMemoryRateLimiter.get_stats()["active_buckets"]`. `InMemoryRateLimiter.consume()` increments the counter directly. The auth rate limiter (`infrastructure/auth/rate_limiter.py`) also increments the counter on lockout events. All three existing wire points (command bus, validation, auth) use the updated counter signature.

### Testing

- [x] **TEST-02**: Hypothesis `@given`-based fuzz tests exist for the event deserialization pipeline. Tests cover: (1) `EventSerializer.deserialize()` on arbitrary bytes and arbitrary dicts asserts only `EventSerializationError` escapes, never `KeyError`, `AttributeError`, or unhandled exceptions; (2) `UpcasterChain.upcast()` on dicts with arbitrary unknown keys and arbitrary `schema_version` values asserts only `UpcastingError` escapes; (3) all 17 event types in `EVENT_TYPE_MAP` are constructed from dicts with arbitrary extra keys via `_filter_constructor_kwargs()` without raising; (4) round-trip property: any event instance serialized then deserialized equals the original. Tests live in `packages/core/tests/unit/test_event_deserialization_fuzz.py`.

### UI Enhancements (promoted from Out of Scope)

- [x] **UI-17**: A provider topology visualization page renders the live relationship graph between providers and groups as an interactive D3.js force-directed graph. Nodes represent providers (colored by state) and groups; edges represent group membership. Node click navigates to provider/group detail. Data sourced from existing `GET /api/providers` and `GET /api/groups` REST endpoints; graph re-renders on WebSocket state updates.

- [x] **UI-18**: Metric time-series are persisted in a SQLite-backed `MetricsHistoryStore` that records a snapshot of all current metric values every 60 seconds (configurable). A `GET /api/metrics/history?provider=X&metric=Y&from=T&to=T` endpoint queries the store. The metrics page gains a time-range selector (1h, 6h, 24h, 7d) and switches from the current point-in-time Recharts visualization to a time-series line chart backed by the history endpoint. History retention is configurable (default: 7 days); a background worker prunes records older than retention.

## Deferred (carried from v1.0, not promoted to v3.0)

The following remain deferred. Not in v3.0 scope.

- No items: all six v1.0 deferred requirements are promoted to v3.0 above.

## v3.0 Out of Scope

Explicitly excluded from v3.0. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Multi-tenant UI | Tenant isolation adds complexity beyond current scope |
| Kubernetes operator UI | Operator has its own CRD-based management |
| Provider code editor | MCP Hangar manages providers, does not develop them |
| Grafana embedding | Use Grafana directly for advanced dashboards |
| Mobile responsive design | Management UI is desktop-focused |
| i18n/localization | English-only |
| Config editor with YAML validation | High write-path risk, read-only is sufficient |
| Alert rules configuration UI | Config-driven alerting is a separate concern |
| Distributed saga coordination | Single-process by design |
| Async/asyncio rewrite of domain layer | Thread-based by design per CLAUDE.md |

## v3.0 Traceability

| PERS-07 | Phase 18 | Complete |
| PERS-08 | Phase 18 | Complete |
| RESL-04 | Phase 17 | Complete |
| TEST-02 | Phase 17 | Complete |
| PERS-06 | Phase 19 | Complete |
| UI-17 | Phase 20 | Complete |
| UI-18 | Phase 20 | Complete |

**Coverage:**

- v3.0 requirements: 8 total (1 EXCP + 3 PERS + 1 RESL + 1 TEST + 2 UI)
- Mapped to phases: 8
- Unmapped: 0

---
*v3.0 requirements defined: 2026-03-14*
*Last updated: 2026-03-14 -- v3.0 Infrastructure Maturity COMPLETE: all 8 requirements satisfied*

## v4.0 Requirements

Requirements for v4.0 Log Streaming. The goal is to stream live stdout/stderr from running provider subprocesses and containers to browser clients over WebSocket.

### Log Capture Infrastructure

- [x] **LOG-01**: `LogLine` frozen dataclass (`provider_id`, `stream: Literal["stdout","stderr"]`, `content: str`, `recorded_at: float`) and `IProviderLogBuffer` ABC (`append`, `tail`, `clear`, `provider_id`) exist in `domain/`. `ProviderLogBuffer` implements `IProviderLogBuffer` with a `collections.deque(maxlen=N)` ring buffer (default 1000 lines). Singleton registry `get_log_buffer(provider_id)` / `set_log_buffer(provider_id, buffer)` exists.

- [x] **LOG-02**: `SubprocessLauncher` and `DockerLauncher` spawn a daemon thread that reads `process.stderr` line-by-line and appends `LogLine(stream="stderr")` entries to the provider's buffer. `DockerLauncher` is changed from `stderr=subprocess.DEVNULL` to `stderr=subprocess.PIPE` and gains an identical reader thread. All reader threads terminate cleanly when the process exits.

- [x] **LOG-03**: `GET /api/providers/{provider_id}/logs` accepts `lines` (default 100, max 1000) and returns `{"logs": [{...LogLine...}], "provider_id": "...", "count": N}`. Returns 404 for unknown providers, empty list for providers with no log buffer yet.

### Log Streaming WebSocket + UI

- [x] **LOG-04**: `LogStreamBroadcaster` has per-provider registered async callbacks. `IProviderLogBuffer.append()` notifies the broadcaster. A WebSocket endpoint `GET /api/ws/providers/{provider_id}/logs` sends the buffered history on connect (as individual `{"type":"log_line",...}` messages), then streams live lines until disconnect. Disconnection cleans up the registered callback (no leak). Bootstrap wires `LogStreamBroadcaster` and `ProviderLogBuffer` instances per configured provider.

- [x] **LOG-05**: `LogViewer` React component renders log lines in a monospace font with stderr in amber and stdout in gray. `useProviderLogs` hook manages the WebSocket connection with auto-reconnect. `ProviderDetailPage` has a "Process Logs" section at the bottom using `LogViewer`. `npx tsc --noEmit` exits 0.

## v4.0 Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| LOG-01 | Phase 21 | Complete |
| LOG-02 | Phase 21 | Complete |
| LOG-03 | Phase 21 | Complete |
| LOG-04 | Phase 22 | Complete |
| LOG-05 | Phase 22 | Complete |

**Coverage:**

- v4.0 requirements: 5 total (5 LOG)
- Mapped to phases: 5
- Unmapped: 0

---
*v4.0 requirements defined: 2026-03-15*
*Last updated: 2026-03-15 -- v4.0 Log Streaming COMPLETE: all 5 requirements satisfied*
