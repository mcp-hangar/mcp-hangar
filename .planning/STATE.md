---
gsd_state_version: 1.0
milestone: v2.0
milestone_name: Management UI
status: unknown
last_updated: "2026-03-14T15:59:17.575Z"
progress:
  total_phases: 8
  completed_phases: 8
  total_plans: 24
  completed_plans: 24
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-14)

**Core value:** Reliable, observable MCP provider management with production-grade lifecycle control
**Current focus:** v2.0 Management UI -- browser-based management interface with REST API, WebSocket streaming, and React frontend

## Current Position

Milestone: v2.0 Management UI -- IN PROGRESS
Phase: 12 of 16 (WebSocket Infrastructure) -- COMPLETE
Plan: next phase (Phase 13)
Status: Phase 12 both plans complete. WebSocket infrastructure (EventBus extension, connection manager, queue, filters) and endpoint handlers (events + state) implemented and wired into ASGI routing.
Last activity: 2026-03-14 -- Phase 12-02 executed: ws_events_endpoint, ws_state_endpoint, ws_routes, ASGI websocket routing (20 tests, all passing)

Progress: [__________] 0% milestone (phase 12 complete)

## Performance Metrics

**v0.9 Velocity:**

- Total plans completed: 7
- Average duration: 4.7 minutes
- Total execution time: 0.78 hours

**v0.10 Velocity:**

- Total plans completed: 6
- Average duration: varies (2-67 minutes)
- Timeline: 2 days (2026-02-28 to 2026-03-01)

**v1.0 Velocity:**

- Total plans completed: 12
- Timeline: 1 day (2026-03-08)
- Files changed: 107 files, +5,073/-381 lines
- Plan durations: 3-16 min (avg ~6 min)

**Cumulative (v0.9 through v1.0):**

- Total plans: 25 across 10 phases in 3 milestones
- Total files changed: ~177 files, ~18,851 lines added
- Total sessions: ~9

See `.planning/RETROSPECTIVE.md` for full cross-milestone trends.

## Accumulated Context

### Key Research Findings (v2.0)

Research completed in `.planning/research/` covers architecture, features, stack, and pitfalls.

**Backend discoveries:**

- Zero new Python dependencies needed -- Starlette and websockets already in dependency tree
- All 18 MCP tools map cleanly to REST endpoints via existing CQRS handlers
- EventBus.subscribe_to_all() exists but EventBus lacks unsubscribe -- must add for WS cleanup
- Backend is thread-based -- REST handlers MUST use `run_in_threadpool()` for CQRS dispatch
- 60+ Prometheus metrics exist with in-memory registry
- All domain objects have `to_dict()` serialization

**Architecture decisions:**

- REST layer wraps CQRS -- no new business logic, pure transport
- WebSocket for bidirectional event streaming (not SSE -- need subscription filters)
- Thread-safe queue bridges sync EventBus to async WS broadcast
- Frontend in `packages/ui/` as separate monorepo package
- API under `/api/` prefix, mounted alongside existing ASGI routes

**Key pitfalls identified:**

- Async/sync bridge: `run_in_threadpool()` required for all CQRS dispatch
- WS lifecycle: EventBus needs `unsubscribe_from_all()` for cleanup
- CORS: Vite dev on 5173, backend on 8000 -- CORSMiddleware required
- Race conditions: WS events must invalidate TanStack Query cache, not replace data
- SPA fallback: backend must serve `index.html` for non-API routes in production

### Decisions

All v0.9, v0.10, and v1.0 decisions archived in PROJECT.md Key Decisions table.

**v2.0 decisions (planning phase):**

- Starlette routes (not FastAPI) -- already using Starlette, no new dependency
- WebSocket over SSE -- bidirectional needed for subscription filters
- Separate `packages/ui/` -- consistent with monorepo structure
- React + TypeScript + Vite + TanStack Query + Zustand + Tailwind + Radix + Recharts
- Static build served by backend in production (single deployment)

**v2.0 decisions (Phase 11-01 execution):**

- Path prefix /api stripped manually in combined_app before forwarding scope to api_app
- CORS origins read from MCP_CORS_ORIGINS env var, defaulting to localhost:5173 for dev
- Error envelope format: {error: {code, message, details}} -- consistent across all 4xx/5xx
- dispatch_query/dispatch_command use run_in_threadpool for async-safe CQRS dispatch
- /api/ routes bypass auth gate in create_auth_combined_app (API handles auth separately)

**v2.0 decisions (Phase 11-02 execution):**

- ProviderGroup serialized via to_status_dict() for consistent member representation
- DiscoveryNotConfigured extends ProviderNotFoundError to inherit HTTP 404 mapping
- system.py uses only dispatch_query (no get_context import) -- minimal dependency surface
- Config sanitization is top-level only (non-recursive) as a practical defense-in-depth measure
- [Phase 11-backend-rest-api]: EventStore API used is get_all_stream_ids()+load() not read_all() -- plan spec had incorrect interface, implementation adapted to real API
- [Phase 11-backend-rest-api]: GetToolInvocationHistoryHandler does not extend BaseQueryHandler -- reads from event store directly, not provider repository

**v2.0 decisions (Phase 11-05 execution):**

- SecurityEventHandler sink accessed defensively via getattr(_sink) then getattr(sink) -- private attribute name not guaranteed by public API
- Alert level filtering performed in API layer not handler -- alerts_sent returns all, filtering is a query concern
- Metrics JSON summary built by parsing Prometheus text lines with known prefixes -- avoids coupling to prometheus_client internals

**v2.0 decisions (Phase 11-04 execution):**

- init_auth_cqrs checks getattr(auth_components, "enabled", False) not just is None -- AuthComponents may exist with enabled=False
- TestClient.delete() does not accept json= kwarg in this Starlette version -- tests use request("DELETE", url, json=...) pattern
- revoke_api_key body parsing uses try/except -- DELETE body is optional per HTTP spec, fault-tolerant parsing is correct

**v2.0 decisions (Phase 12 execution):**

- ws_events_endpoint subscribes/unsubscribes EventBus handler per-connection in try/finally -- guarantees cleanup on any disconnect path
- EventStreamQueue uses call_soon_threadsafe + _safe_put wrapper to silently drop on QueueFull
- connection_manager is a module-level singleton instantiated at import time
- auth_combined_app: only lifespan scopes pass to mcp_app directly; websocket /api/* routed to api_app without auth
- Severity filter deferred -- DomainEvent has no severity field; only event_types and provider_ids filters implemented
- combined_app health/metrics gate is HTTP-only; websocket scopes route to /api/* or mcp_app

### Pending Todos

None beyond phase planning/execution.

### Blockers/Concerns

- JSON serialization of domain objects may need dedicated serializers (Pitfall 6) -- to be assessed in Phase 13+

## Session Continuity

Last session: 2026-03-14
Stopped at: Phase 12 complete -- WebSocket infrastructure + endpoint handlers + ASGI routing wired
Resume with: Start Phase 13 (next phase after WebSocket infrastructure)
