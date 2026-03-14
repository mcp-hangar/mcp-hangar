---
phase: 12-websocket-infrastructure
plan: "02"
subsystem: websocket-endpoints
tags: [websocket, event-streaming, asgi-routing, tdd]
dependency_graph:
  requires: [phase-12-plan-01]
  provides: [ws_events_endpoint, ws_state_endpoint, ws_routes, ASGI websocket routing]
  affects: [server/api/ws/, fastmcp_server/asgi.py, server/api/router.py]
tech_stack:
  added: []
  patterns: [WebSocketRoute, Mount, ASGI scope routing, asyncio.wait_for idle ping/pong, periodic snapshot loop]
key_files:
  created:
    - packages/core/mcp_hangar/server/api/ws/events.py
    - packages/core/mcp_hangar/server/api/ws/state.py
    - packages/core/tests/unit/test_ws_endpoints.py
    - packages/core/tests/unit/test_ws_routing.py
  modified:
    - packages/core/mcp_hangar/server/api/ws/__init__.py
    - packages/core/mcp_hangar/server/api/router.py
    - packages/core/mcp_hangar/fastmcp_server/asgi.py
decisions:
  - ws_events_endpoint subscribes/unsubscribes EventBus handler per-connection in try/finally -- guarantees cleanup on any disconnect path
  - auth_combined_app only passes lifespan scopes to mcp_app directly; websocket /api/* routed to api_app without auth (API handles auth separately)
  - Severity filter deferred -- DomainEvent has no severity field; only event_types and provider_ids filters implemented
  - combined_app health/metrics gate is HTTP-only; websocket scopes fall through to /api/* routing or mcp_app
metrics:
  duration: 15 minutes
  completed: "2026-03-14"
  tasks: 2
  files_changed: 7
---

# Phase 12 Plan 02: WebSocket Endpoints and ASGI Routing Summary

**One-liner:** ws_events_endpoint and ws_state_endpoint implemented with TDD, ws_routes exported, ASGI combined_app updated to route websocket scopes on /api/ws/* to api_app.

## Tasks Completed

| Task | Description | Commit | Tests |
|------|-------------|--------|-------|
| 1 | WebSocket endpoint handlers (events + state) | 7d8fa24 | 10 passing |
| 2 | ASGI routing update + API router ws mount | 92cfec1 | 10 passing |

## What Was Built

### ws/events.py -- ws_events_endpoint

Streams live domain events to connected clients via a per-connection `EventStreamQueue`.

Protocol:

1. Accept connection, read optional filter config within 5s (`event_types`, `provider_ids`).
2. Subscribe a handler closure to `EventBus.subscribe_to_all`; handler calls `put_threadsafe` only if event passes `matches_filters`.
3. Loop: `asyncio.wait_for(queue.get(), 30s)` -- on timeout send `{"type": "ping"}`, wait 10s for pong, close if missing.
4. On `WebSocketDisconnect` or loop exit: `unsubscribe_from_all` + `connection_manager.unregister` in `finally`.

### ws/state.py -- ws_state_endpoint

Sends periodic provider/group state snapshots at a configurable interval.

Protocol:

1. Accept connection, read optional `{"interval": N}` within 2s; clamp to [0.5, 60.0]; default 2.0s.
2. Loop: snapshot `context.providers` + `context.groups`, serialize to `{type, timestamp, providers, groups}`, send, sleep.
3. On `WebSocketDisconnect`: loop exits cleanly.

### ws/**init**.py -- ws_routes

Replaced Plan 01 stub with the routes export:

```python
ws_routes = [
    WebSocketRoute("/events", ws_events_endpoint),
    WebSocketRoute("/state", ws_state_endpoint),
]
```

### router.py -- /ws Mount

`Mount("/ws", routes=ws_routes)` added to `create_api_router()` routes list.

### asgi.py -- Websocket Scope Routing

**combined_app**: Changed `if scope_type == "http"` guard to `if scope_type in ("http", "websocket")`. Health/metrics gate remains HTTP-only. `/api/*` routing applies to both HTTP and WebSocket.

**auth_combined_app**: Replaced `if scope_type != "http": return mcp_app` early-return with explicit lifespan pass-through. Websocket scopes on `/api/*` are now routed to `api_app` (no auth); non-`/api/` websocket scopes fall through to `mcp_app`.

## Verification

```
pytest packages/core/tests/unit/test_ws_endpoints.py    -> 10 passed
pytest packages/core/tests/unit/test_ws_routing.py     -> 10 passed
pytest packages/core/tests/unit/ (excl. pre-existing)  -> 2398 passed, 1 skipped
```

## Deviations from Plan

### Notes (not deviations)

**Severity filter deferred**: `DomainEvent` has no `severity` field. The plan's `<behavior>` section already acknowledged this and explicitly said to implement only `event_types` and `provider_ids`. No deviation -- plan-specified scope.

Pre-existing failure `test_cli_status.py::TestGetStatusFromConfig::test_get_status_missing_config` remains unrelated to WebSocket work.

## Self-Check: PASSED

- [x] `packages/core/mcp_hangar/server/api/ws/events.py` exists
- [x] `packages/core/mcp_hangar/server/api/ws/state.py` exists
- [x] `packages/core/mcp_hangar/server/api/ws/__init__.py` contains `ws_routes`
- [x] `packages/core/mcp_hangar/server/api/router.py` contains `Mount("/ws"`
- [x] `packages/core/mcp_hangar/fastmcp_server/asgi.py` handles `websocket` scope type
- [x] `packages/core/tests/unit/test_ws_endpoints.py` exists
- [x] `packages/core/tests/unit/test_ws_routing.py` exists
- [x] Commit 7d8fa24 exists (ws endpoint handlers)
- [x] Commit 92cfec1 exists (ws routing + ASGI update)
