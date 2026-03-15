# Milestones

## v4.0 Log Streaming (Shipped: 2026-03-15)

**Phases completed:** 2 phases (21-22), 6 plans
**Timeline:** 2026-03-14 -> 2026-03-15
**Files changed:** 31 files, +3,349/-100 lines
**Requirements satisfied:** 5 (LOG-01..05)

**Key accomplishments:**

- `LogLine` frozen dataclass and `IProviderLogBuffer` ABC in `domain/value_objects/`; `ProviderLogBuffer` with `deque(maxlen=1000)` ring buffer and thread-safe singleton registry (LOG-01)
- Daemon stderr-reader threads added to `SubprocessLauncher` and `DockerLauncher`; `DockerLauncher` changed from `stderr=DEVNULL` to `stderr=PIPE` -- critical for live capture; all threads terminate cleanly on process exit (LOG-02)
- `GET /api/providers/{id}/logs` REST endpoint with `lines` clamping [1, 1000], 404 for unknown providers, empty list for cold/unstarted providers (LOG-03)
- `LogStreamBroadcaster` with per-provider async callbacks; `IProviderLogBuffer.append()` notifies broadcaster outside lock per no-I/O-under-lock rule; WebSocket endpoint `GET /api/ws/providers/{id}/logs` sends buffered history on connect then streams live lines; `try/finally` cleanup on disconnect (LOG-04)
- Bootstrap wires `LogStreamBroadcaster` singleton on `ApplicationContext` and injects `ProviderLogBuffer` per configured provider via deferred injection pattern (LOG-04)
- `LogViewer` React component (monospace font, amber stderr, gray stdout); `useProviderLogs` hook with auto-reconnect matching existing hook patterns; `ProviderDetailPage` "Process Logs" section; `npx tsc --noEmit` exits 0 (LOG-05)

**Archive:** `.planning/milestones/v4.0-ROADMAP.md`, `.planning/milestones/v4.0-REQUIREMENTS.md`

---

## v3.0 Infrastructure Maturity (Shipped: 2026-03-14)

**Phases completed:** 4 phases (17-20), 11 plans
**Requirements:** EXCP-02, PERS-06, PERS-07, PERS-08, RESL-04, TEST-02, UI-17, UI-18

**Goal:** Close all six requirements deferred from v1.0, extend observability with circuit breaker instrumentation and rate-limit metric completeness, implement event store compaction to bound history growth, harden the circuit breaker with full HALF_OPEN probe semantics, put saga compensation into production use, and add provider topology visualization and metric time-series persistence to the management UI.

**Phase breakdown:**

- **Phase 17: Quick Wins** (3 plans) -- BLE001 exception hygiene enforcement (EXCP-02), rate-limit metrics with `result` label and active-buckets gauge (RESL-04), Hypothesis fuzz tests for event deserialization (TEST-02)
- **Phase 18: Circuit Breaker & Event Store Compaction** (3 plans) -- Full HALF_OPEN state machine with probe semantics (PERS-07), `mcp_hangar_circuit_breaker_state` Gauge and `CircuitBreakerStateChanged` event, snapshot compaction with `compact_stream()` on both event store backends and admin endpoint (PERS-08)
- **Phase 19: Saga Compensation** (2 plans) -- `ProviderFailoverSaga` converted to step-based `Saga` with compensation commands, `SagaManager` delayed-command facility replacing inline TODO schedulers, integration tests covering COMPENSATING → COMPENSATED path (PERS-06)
- **Phase 20: UI Enhancements** (3 plans) -- `MetricsHistoryStore` SQLite backend with snapshot worker, `GET /api/metrics/history` endpoint, MetricsPage time-series line chart with range selector (UI-18); D3.js provider topology graph with state-colored nodes and WebSocket-driven updates (UI-17)

**Archive:** `.planning/milestones/v3.0-ROADMAP.md` (if created), `.planning/ROADMAP.md` (phases 17-20)

---

## v2.0 Management UI (Shipped: 2026-03-14)

**Phases completed:** 6 phases (11-16), 34 plans
**Timeline:** 2026-03-14 (single day)
**Requirements satisfied:** 35 (REST-01..10, WS-01..05, UI-01..16, INTG-01..04)

**Key accomplishments:**

- REST API layer under `/api/` wrapping all CQRS commands/queries via `run_in_threadpool()` -- providers, groups, auth, config, discovery, events, metrics, audit, system (REST-01..10, INTG-01)
- WebSocket endpoint at `/api/ws/events` for real-time domain event streaming with subscription filters; `/api/ws/state` for periodic provider/group state snapshots (WS-01, WS-02)
- Thread-safe sync-to-async event queue bridges EventBus (thread-based) to WebSocket broadcast (async); ping/pong heartbeat for dead connection detection (WS-03..05)
- `EventBus.unsubscribe_from_all()` added to support per-connection cleanup without handler leaks (WS-04)
- `packages/ui/` React + TypeScript + Vite project with TanStack Query v5, Zustand, Tailwind CSS, Radix UI primitives, Recharts (UI-01..04, INTG-03)
- Dashboard page: provider state distribution chart, metric cards, live event feed via WebSocket, alert summary (UI-05)
- Providers page: filterable/sortable table with start/stop actions; detail view with health, tools, circuit breaker, event timeline (UI-06, UI-07)
- Groups page: list with strategy/member counts, detail with rebalance action; Discovery page with approve/reject; Config viewer with hot reload (UI-08, UI-13, UI-14)
- Metrics page: RED metrics per provider with Recharts visualizations, SLI availability and error budget (UI-09)
- Events page: live WebSocket event stream with type/severity filters + paginated audit log (UI-10)
- Executions page: tool invocation history timeline, failures view, success rate and p95 statistics (UI-11)
- Security events page with severity indicators (UI-12)
- Auth page: API key management (list, create, revoke) and role management (builtin/custom, assign/revoke) (UI-15, UI-16)
- Backend serves Vite static build in production with SPA routing fallback for all non-API routes (INTG-02)
- Multi-stage Dockerfile (node:20-slim build stage + py-builder + runtime) produces single image with UI baked in (INTG-04)

**Archive:** `.planning/ROADMAP.md` (phases 11-16), `.planning/REQUIREMENTS.md` (v2.0 requirements)

---

## v1.0 Production Hardening (Shipped: 2026-03-08, released as v0.11.0: 2026-03-09)

**Phases completed:** 3 phases (8-10), 12 plans
**Timeline:** 1 day (2026-03-08)
**Files changed:** 107 files, +5,073/-381 lines

**Key accomplishments:**

- ProviderGroup lock hierarchy fixed with two-phase lock pattern -- snapshot under lock, I/O outside, re-acquire to update (CONC-01)
- Provider cold starts release lock before I/O, concurrent waiters coordinated via threading.Event instead of blocking on lock (CONC-02, CONC-03)
- StdioClient request-response ordering race eliminated -- PendingRequest registered before writing to stdin (CONC-04)
- Exception hygiene audit: all 42 bare `except Exception:` catches categorized and resolved (fault-barrier, cleanup, bug-hiding) (EXCP-01)
- Discovery-sourced provider commands validated against allowlist via InputValidator (SECR-01)
- Saga state checkpointed to SQLite after each step transition with idempotency guards preventing duplicate commands on replay (PERS-01, PERS-02)
- Circuit breaker state (state, failure_count, opened_at) persisted in provider snapshots across restarts (PERS-03)
- Event store snapshots with aggregate replay from latest snapshot, bounding startup time (PERS-04, PERS-05)
- Health check exponential backoff with jitter for degraded providers, state-aware BackgroundWorker scheduling (RESL-01, RESL-02)
- Command bus middleware pipeline with RateLimitMiddleware covering all transports (SECR-02)
- Docker discovery automatic reconnection with exponential backoff on daemon connection loss (RESL-03)
- Property-based state machine tests with Hypothesis RuleBasedStateMachine (TEST-01)
- PEP 561 py.typed marker with incremental mypy strictness (QUAL-01)

**Archive:** `.planning/ROADMAP.md` (phases 8-10), `.planning/REQUIREMENTS.md` (v1.0 requirements)

---

## v0.10 Documentation & Kubernetes Maturity (Shipped: 2026-03-01)

**Phases completed:** 3 phases (5-7), 6 plans
**Timeline:** 2 days (2026-02-28 -> 2026-03-01)
**Files changed:** 40 files, +8,766/-65 lines

**Key accomplishments:**

- Configuration Reference page documenting all 13 YAML config sections and 28+ environment variables with defaults and validation rules
- MCP Tools Reference page documenting all 22 tools across 7 categories with parameters, return formats, error codes, and side effects
- Provider Groups Guide covering all 5 load balancing strategies, health policies, circuit breaker, and tool access filtering with usage examples
- Facade API Guide documenting Hangar/SyncHangar public API with method signatures, HangarConfig builder, and framework integration patterns
- MCPProviderGroup Kubernetes controller with label-based selection, status aggregation, and threshold-based health policy evaluation
- MCPDiscoverySource Kubernetes controller with 4 discovery modes (Namespace, ConfigMap, Annotations, ServiceDiscovery), additive/authoritative sync, and owner references
- envtest-based integration tests for both controllers covering happy path and failure scenarios
- Both Helm charts synchronized to v0.10.0 with NOTES.txt post-install guidance and test templates for installation validation

**Archive:** `.planning/milestones/v0.10-ROADMAP.md`, `.planning/milestones/v0.10-REQUIREMENTS.md`

---

## v0.9 Security Hardening (Shipped: 2026-02-15)

**Phases completed:** 4 phases, 7 plans
**Timeline:** 2026-02-15 (single day, 0.61 hours execution time)
**Files changed:** 30 files, +5012/-55 lines

**Key accomplishments:**

- Constant-time API key validation (hmac.compare_digest) across all 4 auth stores, eliminating timing side-channel attacks
- Exponential backoff rate limiting (2x escalation, capped at 1h) with RateLimitLockout/Unlock domain events for audit trail
- JWT max token lifetime enforcement (configurable, default 3600s) with specific TokenLifetimeExceededError messages
- Zero-downtime API key rotation with configurable grace period (default 24h) across InMemory, SQLite, Postgres, and EventSourced stores

**Archive:** `.planning/milestones/v0.9-ROADMAP.md`, `.planning/milestones/v0.9-REQUIREMENTS.md`

---
