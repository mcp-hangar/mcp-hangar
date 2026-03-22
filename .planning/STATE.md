---
gsd_state_version: 1.0
milestone: v5.0
milestone_name: Platform Management Console
status: in_progress
last_updated: "2026-03-22T14:01:00Z"
progress:
  total_phases: 8
  completed_phases: 1
  total_plans: 37
  completed_plans: 10
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-22)

**Core value:** Reliable, observable MCP provider management with production-grade lifecycle control
**Current focus:** v5.0 Platform Management Console -- PLANNING. Transform from config-file-driven to full platform management console with CRUD, catalog, RBAC, tool access policies, and config export.

## Current Position

Milestone: v5.0 Platform Management Console -- IN PROGRESS (Phase 23 + Phase 25 complete)
Status: Phase 23 Plans 01-05 complete (backend CRUD + integration tests). Phase 25 Plans 01-05 complete (UI CRUD forms). Phase 23 now fully complete.
Last activity: 2026-03-22 -- Phase 23, Plan 05 executed (14 integration tests: provider CRUD, group CRUD, config serializer round-trip, 2756 tests passing total).

Progress: [|||.......] 27% -- 10 of 37 total plans complete (Phase 23: 5/5, Phase 25: 5/5)

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

- SecurityEventHandler sink accessed defensively via getattr(\_sink) then getattr(sink) -- private attribute name not guaranteed by public API
- Alert level filtering performed in API layer not handler -- alerts_sent returns all, filtering is a query concern
- Metrics JSON summary built by parsing Prometheus text lines with known prefixes -- avoids coupling to prometheus_client internals

**v2.0 decisions (Phase 11-04 execution):**

- init_auth_cqrs checks getattr(auth_components, "enabled", False) not just is None -- AuthComponents may exist with enabled=False
- TestClient.delete() does not accept json= kwarg in this Starlette version -- tests use request("DELETE", url, json=...) pattern
- revoke_api_key body parsing uses try/except -- DELETE body is optional per HTTP spec, fault-tolerant parsing is correct

**v2.0 decisions (Phase 12 execution):**

- ws_events_endpoint subscribes/unsubscribes EventBus handler per-connection in try/finally -- guarantees cleanup on any disconnect path
- EventStreamQueue uses call_soon_threadsafe + \_safe_put wrapper to silently drop on QueueFull
- connection_manager is a module-level singleton instantiated at import time
- auth_combined_app: only lifespan scopes pass to mcp_app directly; websocket /api/\* routed to api_app without auth
- Severity filter deferred -- DomainEvent has no severity field; only event_types and provider_ids filters implemented
- combined_app health/metrics gate is HTTP-only; websocket scopes route to /api/\* or mcp_app

**v2.0 decisions (Phase 13 execution):**

- NavLink end=true on Dashboard route to prevent it matching all child paths
- Sidebar inlines its own icon-mapped NAV_ITEMS (typed lucide-react components) rather than importing from constants.ts which stores string icon names
- SystemStatusBadge polls /api/system/info every 30s; displays version + ready/total count when backend is up, "Backend offline" on error
- Zustand stores use module-level singletons -- no React Provider wrapper needed

**v2.0 decisions (Phase 14 execution):**

- GroupsPage uses selectedGroupId state to drive conditional detail query (enabled: !!selectedGroupId)
- Rebalance and approve/reject mutations invalidate parent .all query key to refresh all sub-keys
- ConfigPage 5-second feedback via useEffect + setTimeout clearing reloadMessage state
- Quarantine section is informational only -- approve/reject actions only at pending stage

**v4.0 decisions (Phase 21-01 execution):**

- DEFAULT_MAX_LINES = 1000 per provider -- configurable at construction time
- on_append callback invoked outside the lock to prevent I/O under lock antipattern
- get_or_create_log_buffer uses \_registry_lock for thread-safe idempotency
- Lazy imports in init_log_buffers() avoid circular dependency between server/bootstrap and infrastructure layers

**v4.0 decisions (Phase 21-02 execution):**

- Reader thread uses BLE001 noqa fault-barrier -- pipe errors silently swallowed to prevent thread crash
- \_start_stderr_reader is a no-op when process or stderr is None -- safe for HTTP remote transport
- \_create_client guards \_start_stderr_reader with_log_buffer is not None check

**v4.0 decisions (Phase 21-03 execution):**

- lines param invalid value falls back to 100, not 400 error -- tolerant parsing for non-critical param
- Provider existence check before buffer lookup ensures consistent 404 semantics for unknown providers
- get_log_buffer None guard returns empty list -- cold/unstarted providers shouldn't 404

**v4.0 decisions (Phase 22-01 execution):**

- Broadcaster callbacks are per-provider async callables -- enables concurrent streaming to multiple clients
- on_append invoked outside ProviderLogBuffer.\_lock per CLAUDE.md no-I/O-under-lock rule
- try/finally in WS handler guarantees cleanup regardless of disconnect reason

**v4.0 decisions (Phase 22-02 execution):**

- Deferred buffer injection: Provider constructed first, buffer injected after by init_log_buffers() -- avoids constructor signature change
- LogStreamBroadcaster singleton carried on ApplicationContext for WS endpoint access

**v4.0 decisions (Phase 22-03 execution):**

- Amber for stderr, gray for stdout -- conventional color coding matching terminal standards
- useProviderLogs follows same auto-reconnect pattern as useWebSocket hook -- consistent with existing hooks

**v5.0 decisions (Phase 23-01 execution):**

- IdleTTL and HealthCheckInterval use .seconds attribute (not .value) -- plan snippet had incorrect attribute, auto-fixed in GREEN phase
- state_snapshot property added to Provider for lock-free CPython read -- needed by ProviderGroup callers constrained by lock hierarchy
- UpdateProviderHandler uses provider.collect_events() after update_config() to forward ProviderUpdated event -- respects aggregate event sourcing pattern
- DeleteProviderHandler calls provider.shutdown() only for non-COLD/non-DEAD states -- matches state machine invariants

**v5.0 decisions (Phase 23-02 execution):**

- Each group handler owns its own threading.Lock (not a single shared groups lock) to minimize contention and match the per-provider pattern
- ProviderGroup.update() acquires self.\_lock internally; UpdateGroupHandler does not hold its own lock during the call (avoids nested locking)
- DeleteGroupHandler: del from GROUPS inside lock, then stop_all() outside lock to avoid holding lock during I/O
- AddGroupMemberHandler: repository.get() before acquiring lock so the lookup does not block other group mutations
- UpdateGroupCommand does not have a strategy field (Plan 01 only added description/min_healthy); ProviderGroup.update() still accepts strategy for future use

**v5.0 decisions (Phase 23-03 execution):**

- serialize_full_config(providers=None, groups=None) accepts optional explicit dicts so tests bypass get_context() -- testability without patching
- to_config_dict() omits description key entirely when None (not just setting it to None) -- matches config.py load behavior
- yaml.safe_dump with sort_keys=True, allow_unicode=True for deterministic YAML output

**v5.0 decisions (Phase 23-04 execution):**

- UpdateGroupCommand has no strategy field (confirmed by Plan 02 design); removed from update_group handler
- Config routes ordered /export and /backup before /reload to avoid ambiguous path matching
- register_crud_handlers() imported lazily inside init_cqrs() to mirror existing PROVIDER_REPOSITORY lazy import pattern
- member_id body key mapped to provider_id command field in add_group_member -- REST API uses member_id for clarity; command uses provider_id for domain consistency

**v5.0 decisions (Phase 23-05 execution):**

- GroupMember.id property returns str(provider.id) -- plan example code used hasattr guard but actual API has clean id property
- write_config_backup() calls serialize_full_config() without args internally; integration tests must patch it to avoid live get_context() dependency

### v5.0 Key Discoveries

- **Existing domain events**: `events.py` already has `RoleAssigned`, `RoleRevoked`, `CatalogItemPublished`, `CatalogItemApproved`, `CatalogItemRejected`, `CatalogItemDeprecated` and many auth events. Some v5.0 events partially exist.
- **Existing group events**: `GroupCreated`, `GroupMemberAdded`, `GroupMemberRemoved`, `GroupMemberHealthChanged`, `GroupStateChanged`, `GroupCircuitOpened`, `GroupCircuitClosed` already exist in `domain/model/provider_group.py`. New events `GroupUpdated`, `GroupDeleted` follow the same `Group*` naming convention (not `ProviderGroup*`).
- **Provider API is read-only**: Current `server/api/providers.py` only has GET (list/detail), POST start/stop. No POST create, PUT update, DELETE. Same for groups API.
- **ProviderRegistered is a NEW event**: `domain/events.py` has `ProviderDiscovered` (from discovery flow), `ProviderApproved`, `ProviderHotLoaded` -- but no `ProviderRegistered`. CRUD-01 creates this as a new event with `source: str` field (`"api"` / `"config"` / `"discovery"`) to distinguish creation origin.
- **Optimistic concurrency deferred**: REST CRUD uses last-write-wins. Event sourcing `expected_version` exists but is not exposed via ETag/If-Match headers. Acceptable for single-admin v5.0 scope.
- **Catalog seed entries need builtin flag**: `McpProviderEntry.builtin: bool` prevents accidental deletion of seed entries via API. Only custom entries can be removed.
- **Auth API partial coverage**: POST/GET/DELETE /api/auth/keys, GET/POST /api/auth/roles, POST assign, DELETE revoke. v5.0 extends with custom role CRUD (PUT/DELETE for roles) and principal management.
- **Config loading is one-way**: `server/config.py` has `load_config()` that parses YAML to Provider/ProviderGroup objects. v5.0 needs the inverse: serialize in-memory state back to YAML.
- **ToolAccessPolicy value object exists**: allow/deny lists with merge semantics. v5.0 adds REST endpoints to manage policies.
- **RBAC infrastructure exists**: Built-in roles in `domain/security/roles.py`, SQLite auth store at `infrastructure/auth/sqlite_store.py`. v5.0 adds custom role CRUD via `RoleStore`.

### Pending Todos

None.

### Blockers/Concerns

None.

## Session Continuity

Last session: 2026-03-22
Stopped at: Completed Phase 25 (all 5 plans) -- full provider + group CRUD forms in React/TypeScript. ProviderCreateDrawer (4-step wizard), ProviderEditDrawer, ProviderDeleteDialog, ProvidersPage wiring, GroupCreateDrawer, GroupEditDrawer, GroupMemberPanel (add/remove/inline-edit), GroupsPage wiring, ToolAccessPolicyEditor. Phase 23 Plan 05 (tests) still pending.
Resume with: Execute Phase 23 Plan 05 (CRUD integration tests), then Phase 24 (Discovery + Catalog API).
