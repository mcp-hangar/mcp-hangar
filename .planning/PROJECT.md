# MCP Hangar

## What This Is

MCP Hangar is a production-grade infrastructure platform for Model Context Protocol (MCP) providers. It manages provider lifecycle (subprocess, Docker, remote HTTP), load balancing across provider groups, auto-discovery (Kubernetes, Docker, filesystem, entrypoints), and exposes tools via MCP protocol. Includes a Kubernetes operator with MCPProvider, MCPProviderGroup, and MCPDiscoverySource custom resources, plus comprehensive documentation. Designed for environments with thousands of engineers and zero tolerance for mystery failures.

## Core Value

Reliable, observable MCP provider management with production-grade lifecycle control -- providers start, run, degrade, and recover predictably with full audit trail.

## Current State

v4.0 Log Streaming shipped 2026-03-15. All 22 phases (46 plans) across 6 milestones complete. Live stderr capture from provider subprocesses and Docker containers streams to browser clients over WebSocket. Per-provider ring buffers (deque maxlen=1000), REST log history endpoint, LogStreamBroadcaster with per-provider async callbacks, and LogViewer React component (amber stderr / gray stdout, auto-reconnect). Providers no longer crash silently.

## Requirements

### Validated

- JWT authentication with JWKS/OIDC discovery (token validation)
- API key authentication with SHA-256 hashing
- RBAC authorization with role-based tool access
- OPA policy-based authorization
- Auth middleware pipeline (authenticate -> authorize -> execute)
- Auth stores: SQLite, PostgreSQL, event-sourced
- Per-IP rate limiting (AuthRateLimiter) with lockout logic
- Domain events for auth success/failure (audit trail)
- Tool access filtering with allow/deny lists (fnmatch patterns)
- Constant-time API key comparison (hmac.compare_digest) -- v0.9
- Exponential backoff rate limiting with domain events -- v0.9
- JWT max token lifetime enforcement (configurable) -- v0.9
- API key rotation with grace period (rotate_key, KeyRotated event) -- v0.9
- Configuration Reference page (full YAML schema, env vars, validation rules) -- v0.10
- MCP Tools Reference page (all 22 tools with parameters, returns, side effects) -- v0.10
- Provider Groups Guide (5 strategies, health policies, circuit breaker, tool filtering) -- v0.10
- Facade API Guide (Hangar/SyncHangar API, HangarConfig builder, framework integration) -- v0.10
- MCPProviderGroup controller (label selection, status aggregation, health policy evaluation) -- v0.10
- MCPDiscoverySource controller (4 modes, additive/authoritative, owner references) -- v0.10
- envtest integration tests for both Kubernetes controllers -- v0.10
- Helm charts version-synchronized to 0.10.0 with NOTES.txt and test templates -- v0.10
- ProviderGroup lock hierarchy enforced (two-phase lock pattern, no I/O under group lock) -- v1.0
- Provider cold starts release lock before I/O, concurrent waiters via threading.Event -- v1.0
- StdioClient request-response ordering race-free (PendingRequest before write) -- v1.0
- Exception hygiene audit: 42 bare catches resolved (fault-barrier, cleanup, specific) -- v1.0
- Discovery-sourced commands validated against allowlist (InputValidator) -- v1.0
- Saga state checkpointed to SQLite with idempotency guards on replay -- v1.0
- Circuit breaker state persisted in provider snapshots across restarts -- v1.0
- Event store snapshots with aggregate replay from latest snapshot -- v1.0
- Health check exponential backoff with jitter, state-aware scheduling -- v1.0
- Command bus rate limiting middleware covering all transports -- v1.0
- Docker discovery automatic reconnection with exponential backoff -- v1.0
- Property-based state machine tests with Hypothesis RuleBasedStateMachine -- v1.0
- PEP 561 py.typed marker with incremental mypy strictness -- v1.0
- REST API layer (REST-01..REST-10): Starlette routes wrapping CQRS for providers, groups, auth, config, discovery, events, metrics, audit, system -- v2.0
- WebSocket streaming (WS-01..WS-05): real-time event streaming, state snapshots, connection lifecycle, EventBus unsubscribe -- v2.0
- React + TypeScript + Vite frontend (UI-01..UI-16): dashboard, provider list/detail, groups, discovery, metrics, events, audit, executions, security events, auth management -- v2.0
- CORS, SPA fallback, Vite proxy, Docker multi-stage build (INTG-01..INTG-04) -- v2.0
- Ruff BLE001 bare-except lint rule enabled project-wide with fault-barrier annotations (EXCP-02) -- v3.0
- RATE_LIMIT_HITS_TOTAL counter with result label; RATE_LIMIT_ACTIVE_BUCKETS gauge (RESL-04) -- v3.0
- Hypothesis fuzz tests for event deserialization round-trip and adversarial inputs (TEST-02) -- v3.0
- CircuitBreaker full HALF_OPEN state machine with probe_count semantics; CircuitBreakerStateChanged event; mcp_hangar_circuit_breaker_state Gauge (PERS-07) -- v3.0
- IEventStore.compact_stream(); SQLite + InMemory implementations; CompactionError; POST /api/maintenance/compact; mcp_hangar_events_compacted_total counter (PERS-08) -- v3.0
- ProviderFailoverSaga converted to step-based Saga with compensation commands; SagaManager delayed-command facility; compensation integration tests (PERS-06) -- v3.0
- D3.js provider topology visualization page with force-directed graph, state-colored nodes, WebSocket-driven updates (UI-17) -- v3.0
- MetricsHistoryStore SQLite backend with snapshot worker, pruning, GET /api/metrics/history, time-series line chart with range selector (UI-18) -- v3.0
- LogLine value object and IProviderLogBuffer interface in domain/; ProviderLogBuffer ring buffer (deque maxlen=1000); thread-safe singleton registry (LOG-01) -- v4.0
- Live stderr-reader daemon threads in SubprocessLauncher and DockerLauncher; DockerLauncher DEVNULL-to-PIPE change (LOG-02) -- v4.0
- GET /api/providers/{id}/logs REST endpoint with lines clamping, 404 for unknown providers (LOG-03) -- v4.0
- LogStreamBroadcaster with per-provider async callbacks; WebSocket endpoint GET /api/ws/providers/{id}/logs with history-on-connect and live streaming; try/finally cleanup (LOG-04) -- v4.0
- LogViewer React component (monospace, amber stderr, gray stdout); useProviderLogs hook with auto-reconnect; ProviderDetailPage "Process Logs" section (LOG-05) -- v4.0

### Active

No active requirements. v4.0 is the current shipped milestone. See `/gsd-new-milestone` to plan the next milestone.

### Out of Scope

- API key IP binding (allowed_ips per key) -- deferred
- OIDC login flow (authorization code, redirects) -- resource server, not IdP
- mTLS between Hangar and providers -- separate deployment concern
- Vault/HSM integration -- production deployment concern
- Distributed rate limiting (Redis-backed) -- single-node first
- Multi-tenant UI -- adds complexity beyond management UI scope
- Kubernetes operator UI -- operator has CRD-based management
- Provider code editor -- Hangar manages providers, not develops them
- Grafana embedding -- use Grafana directly
- Mobile responsive design -- desktop-focused management UI
- Async/asyncio rewrite of domain layer -- thread-based by design
- stdout capture from providers -- requires different pipe routing; deferred post-v4.0
- Log persistence across restarts -- ring buffers are in-memory only; storage backend deferred

## Context

- Python 3.11+, DDD + CQRS + Event Sourcing architecture
- Go Kubernetes operator with controller-runtime (MCPProvider, MCPProviderGroup, MCPDiscoverySource CRDs)
- Auth layer in `packages/core/mcp_hangar/infrastructure/auth/`
- Domain security in `packages/core/mcp_hangar/domain/security/`
- Operator controllers in `packages/operator/internal/controller/`
- Documentation in `docs/` with MkDocs
- Two Helm charts: `packages/helm-charts/mcp-hangar/` (server) and `packages/helm-charts/mcp-hangar-operator/` (operator)
- Thread-safe design with lock hierarchy (Provider._lock -> StdioClient.pending_lock)
- All auth stores use constant-time key validation
- ASGI app via Starlette (existing routes: /health, /ready, /metrics, /mcp)
- EventBus with subscribe_to_all() for real-time event streaming
- 60+ Prometheus metrics with in-memory registry
- All domain objects have to_dict() serialization

## Constraints

- **Architecture**: Domain layer has NO external dependencies. Layer dependencies flow inward only.
- **Thread safety**: All shared state changes must be thread-safe (existing RLock pattern).
- **Event sourcing**: State changes must emit domain events.
- **Backward compat**: Existing config must continue working. New config fields must have sensible defaults.
- **Python 3.11+**: Modern type hints (str | None, list[str]).
- **Go conventions**: controller-runtime patterns for Kubernetes operator.
- **REST layer**: No new business logic -- pure transport wrapping CQRS. All CQRS dispatch via `run_in_threadpool()`.
- **Frontend**: React + TypeScript + Vite. TanStack Query for server state. Zustand for client state. Tailwind + Radix for UI.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Token validation only (no OIDC flow) | MCP Hangar is resource server | Good |
| SHA-256 for API key hashing | Standard, sufficient for comparison | Good |
| In-memory rate limiter (not distributed) | Single-node first, scale later | Good |
| hmac.compare_digest for all hash comparisons | C-level constant-time, resists timing attacks | Good -- v0.9 |
| Iterate all dict entries without early exit | Prevents timing side-channel on key position | Good -- v0.9 |
| Dummy hash comparison for SQL stores | Defense-in-depth beyond DB index timing | Good -- v0.9 |
| Exponential backoff factor^(count-1) | First lockout at base duration, progressive escalation | Good -- v0.9 |
| event_publisher optional callback pattern | Backward compatibility, safe publishing | Good -- v0.9 |
| max_token_lifetime=0 disables check | Escape hatch for environments needing no limit | Good -- v0.9 |
| 24h default grace period for key rotation | Balances security and operational convenience | Good -- v0.9 |
| Prevent cascading rotations | Avoids multiple grace periods on same key | Good -- v0.9 |
| MCPProviderGroup is read-only aggregator (no owner refs) | Groups observe, don't own providers | Good -- v0.10 |
| MCPDiscoverySource creates MCPProviders with owner refs | Discovery is the parent controller | Good -- v0.10 |
| Group Ready is threshold-based; Degraded+Ready coexist | Matches K8s patterns (partially available) | Good -- v0.10 |
| Authoritative sync deletes immediately (label-based tracking) | Clean semantics, scoped to successful scans | Good -- v0.10 |
| 7 tool categories matching source file organization | Reflects actual code structure over arbitrary grouping | Good -- v0.10 |
| Broken doc link fixes deferred to v0.11 | Not blocking v0.10 goals, separate concern | Pending |
| Two-phase lock pattern for ProviderGroup | Snapshot under lock, I/O outside, re-acquire to update | Good -- v1.0 |
| threading.Event for concurrent startup coordination | Concurrent waiters don't block on provider lock | Good -- v1.0 |
| Annotated all 42 bare except Exception catches | Convention established (fault-barrier vs infra-boundary) | Good -- v1.0 |
| CB state saved at shutdown only (not per-transition) | Avoids write amplification, sufficient for persistence | Good -- v1.0 |
| Saga idempotency via is_processed()/mark_processed() | Prevents duplicate commands during event replay | Good -- v1.0 |
| hasattr-based API detection for old/new event store | Dual hydration path for backward compatibility | Good -- v1.0 |
| CommandBus middleware chain-of-responsibility pattern | Extensible pipeline for rate limiting, future middleware | Good -- v1.0 |
| Inline backoff in DockerDiscoverySource (not imported) | Self-contained discovery source, no cross-module coupling | Good -- v1.0 |
| Starlette routes (not FastAPI) for REST API | Already using Starlette via ASGI. No new dependency. | Good -- v2.0 |
| WebSocket over SSE for event streaming | Bidirectional needed for subscription filters | Good -- v2.0 |
| Async handlers with run_in_threadpool() | Bridge async ASGI to sync CQRS. Standard pattern. | Good -- v2.0 |
| Separate packages/ui/ for frontend | Consistent with monorepo structure. Independent build. | Good -- v2.0 |
| API under /api/ prefix | Clear separation from MCP protocol and system routes | Good -- v2.0 |
| Static build served by backend in production | Single deployment, SPA fallback for client routing | Good -- v2.0 |
| React + TanStack Query + Zustand | TQ for server state, Zustand for client/WS state. Minimal. | Good -- v2.0 |
| Tailwind + Radix (shadcn/ui) + Recharts | Utility CSS + accessible primitives + declarative charts | Good -- v2.0 |
| BLE001 fault-barrier annotation convention | Language constraints prevent narrowing; convention documents intent | Good -- v3.0 |
| RATE_LIMIT_HITS_TOTAL result label (allowed/rejected) | Rejection rate = rejected/(allowed+rejected), not just rejections | Good -- v3.0 |
| CircuitBreaker HALF_OPEN gates exactly probe_count requests | Probe failure resets to OPEN with fresh opened_at, not CLOSED | Good -- v3.0 |
| compact_stream() raises CompactionError without snapshot | Compaction without a reference point would delete unbounded events | Good -- v3.0 |
| ProviderFailoverSaga converted to step-based Saga | Compensation commands become first-class, not comments | Good -- v3.0 |
| SagaManager delayed-command facility replaces inline TODOs | Enforced backoff delays instead of skipped TODOs | Good -- v3.0 |
| MetricsHistoryStore SQLite snapshot every 60s | Bounded write amplification, sufficient granularity for 1h-7d views | Good -- v3.0 |
| DEFAULT_MAX_LINES = 1000 per provider ring buffer | Bounded memory per provider; configurable at construction | Good -- v4.0 |
| on_append callback invoked outside ProviderLogBuffer._lock | No I/O under lock -- CLAUDE.md rule enforced | Good -- v4.0 |
| Deferred buffer injection via init_log_buffers() | Avoids Provider constructor signature change; bootstrap owns wiring | Good -- v4.0 |
| LogStreamBroadcaster singleton on ApplicationContext | Single broadcaster shared by all WebSocket connections | Good -- v4.0 |
| DockerLauncher DEVNULL-to-PIPE change | Live capture impossible with DEVNULL; stderr is critical signal | Good -- v4.0 |
| BLE001 noqa in stderr reader thread | Pipe errors on process kill must be swallowed; thread must not crash | Good -- v4.0 |
| Amber stderr / gray stdout in LogViewer | Conventional terminal color coding; stderr is always the alert signal | Good -- v4.0 |

---
*Last updated: 2026-03-15 -- v4.0 Log Streaming COMPLETE: all 22 phases shipped, Key Decisions confirmed Good*
