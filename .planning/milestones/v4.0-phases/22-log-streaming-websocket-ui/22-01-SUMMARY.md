---
phase: 22-log-streaming-websocket-ui
plan: 01
subsystem: infrastructure
tags: [websocket, log-streaming, broadcaster, async, python, starlette]

# Dependency graph
requires:
  - phase: 21-01
    provides: LogLine, IProviderLogBuffer, ProviderLogBuffer with on_append callback slot
  - phase: 21-02
    provides: stderr-reader threads populating buffers
  - phase: 21-03
    provides: REST log history endpoint (polling foundation)
provides:
  - LogStreamBroadcaster with per-provider async callback registration
  - on_append callback wired in ProviderLogBuffer to notify broadcaster
  - GET /ws/providers/{provider_id}/logs WebSocket endpoint (history on connect + live stream)
  - Disconnection cleanup removes registered callback (no leak)
affects: [22-02, 22-03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - LogStreamBroadcaster with dict[str, list[Callable]] per-provider callbacks
    - WebSocket history-then-stream pattern -- send buffered history on connect, then push live lines
    - try/finally cleanup in WS handler -- callback deregistered on any disconnect path

key-files:
  created:
    - packages/core/mcp_hangar/server/api/ws/logs.py
    - packages/core/mcp_hangar/server/api/ws/__init__.py
    - packages/core/tests/unit/test_log_broadcaster.py
  modified:
    - packages/core/mcp_hangar/infrastructure/persistence/log_buffer.py

key-decisions:
  - "on_append callback registered per-provider in LogStreamBroadcaster -- decoupled from ProviderLogBuffer"
  - "WebSocket sends buffered history on connect as individual log_line messages before streaming live"
  - "try/finally in WS handler guarantees callback cleanup on any disconnect path"

patterns-established:
  - "Pattern 1: Broadcaster with per-provider callback dict -- same pattern as EventBus subscribe/unsubscribe"
  - "Pattern 2: History-then-stream WS protocol -- client gets context immediately on connect"

requirements-completed: [LOG-04]

# Metrics
duration: 1min
completed: 2026-03-15
---

# Phase 22 Plan 01: LogStreamBroadcaster and WebSocket Logs Endpoint Summary

**LogStreamBroadcaster with per-provider async callback registration, ProviderLogBuffer on_append wired to broadcaster, and /ws/providers/{id}/logs WebSocket endpoint sending buffered history on connect then streaming live lines**

## Performance

- **Duration:** 1 min
- **Started:** 2026-03-15T10:03:00Z
- **Completed:** 2026-03-15T10:04:00Z
- **Tasks:** 2 (verification tasks -- code was pre-implemented)
- **Files modified:** 4

## Accomplishments

- Verified LogStreamBroadcaster class with register/unregister per-provider async callbacks
- Verified ProviderLogBuffer.on_append callback slot updated to notify LogStreamBroadcaster on each append
- Verified GET /ws/providers/{provider_id}/logs WebSocket endpoint in server/api/ws/logs.py
- Verified history-then-stream protocol: buffered history sent as individual messages on connect, live lines streamed until disconnect
- Verified try/finally cleanup removes callback on any disconnect path -- no resource leak
- All 17 test_log_broadcaster.py tests pass

## Task Commits

Each task was committed atomically (pre-implemented, committed before plan execution):

1. **Task 1: Verify LogStreamBroadcaster and on_append wiring** - `0540ecb` (feat -- pre-implemented)
2. **Task 2: Verify /ws/providers/{id}/logs WebSocket endpoint** - `0540ecb` (feat -- pre-implemented)

**Plan metadata:** _(this summary commit -- docs: complete plan)_

_Note: Both tasks verified pre-implemented code. The implementation commit 0540ecb contains all files._

## Files Created/Modified

- `packages/core/mcp_hangar/server/api/ws/logs.py` - LogStreamBroadcaster + ws_logs_endpoint WebSocket handler
- `packages/core/mcp_hangar/server/api/ws/__init__.py` - Package init exporting WebSocket routes
- `packages/core/mcp_hangar/infrastructure/persistence/log_buffer.py` - Added on_append callback invocation outside lock
- `packages/core/tests/unit/test_log_broadcaster.py` - 17 unit tests for broadcaster and WebSocket lifecycle

## Decisions Made

- Broadcaster callbacks are per-provider async callables -- enables concurrent streaming to multiple clients
- on_append invoked outside ProviderLogBuffer._lock per CLAUDE.md no-I/O-under-lock rule
- try/finally in WS handler guarantees cleanup regardless of disconnect reason (client close, server shutdown, exception)

## Deviations from Plan

None - plan executed exactly as written. All pre-implemented files matched specifications exactly. All 17 unit tests passed on first run.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Self-Check

- [x] `packages/core/mcp_hangar/server/api/ws/logs.py` -- FOUND
- [x] `packages/core/mcp_hangar/server/api/ws/__init__.py` -- FOUND
- [x] `packages/core/tests/unit/test_log_broadcaster.py` -- FOUND
- [x] Commit `0540ecb` exists in git log
- [x] All 17 test_log_broadcaster.py tests passed

## Self-Check: PASSED

## Next Phase Readiness

- WebSocket streaming infrastructure complete
- Ready for Plan 22-02 (bootstrap wiring: LogStreamBroadcaster + ProviderLogBuffer per provider, integration tests)
- Ready for Plan 22-03 (LogViewer React component + useProviderLogs hook + ProviderDetailPage integration)

---
_Phase: 22-log-streaming-websocket-ui_
_Completed: 2026-03-15_
