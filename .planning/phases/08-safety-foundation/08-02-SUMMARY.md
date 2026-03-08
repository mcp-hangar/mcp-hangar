---
phase: 08-safety-foundation
plan: 02
subsystem: concurrency
tags: [threading, locks, provider, rlock, event-coordination]

# Dependency graph
requires:
  - phase: 08-safety-foundation plan 01
    provides: Lock hierarchy enforcement and StdioClient race fix
provides:
  - Provider ensure_ready()/_start() performs I/O outside lock with threading.Event coordination
  - Provider invoke_tool() refresh uses two-lock-cycle pattern for lock-free RPC
affects: [09-state-survival, 10-operational-hardening]

# Tech tracking
tech-stack:
  added: []
  patterns: [threading.Event concurrent waiter, multi-lock-cycle I/O, refresh deduplication flag]

key-files:
  created: []
  modified:
    - packages/core/mcp_hangar/domain/model/provider.py
    - packages/core/tests/unit/test_provider_aggregate.py

key-decisions:
  - "threading.Event with clear/set for concurrent startup coordination instead of condition variable"
  - "Multi-lock-cycle pattern for invoke_tool() refresh: check-copy-release, RPC outside, reacquire-update"
  - "_refresh_in_progress boolean flag for deduplication instead of per-tool locking"

patterns-established:
  - "Concurrent waiter pattern: threading.Event cleared on start, set on complete/fail, waiters use Event.wait(timeout)"
  - "Two-lock-cycle refresh: Lock cycle 1 claims slot, I/O outside, Lock cycle 2 applies results"

requirements-completed: [CONC-02, CONC-03]

# Metrics
duration: 14min
completed: 2026-03-08
---

# Phase 8 Plan 2: Provider Concurrency Refactor Summary

**Provider lock-free I/O via threading.Event startup coordination and two-lock-cycle tool refresh deduplication**

## Performance

- **Duration:** ~14 min
- **Started:** 2026-03-08T18:29:54Z
- **Completed:** 2026-03-08T18:43:32Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- Provider ensure_ready()/_start() restructured so subprocess launch and MCP handshake happen outside the lock, with concurrent callers waiting on threading.Event instead of blocking
- invoke_tool() tool refresh uses multi-lock-cycle pattern: claim refresh slot under lock, perform tools/list RPC outside lock, apply results under lock -- concurrent callers share single refresh
- 10 new behavior tests covering concurrent startup, error propagation, timeout handling, lock-free refresh, deduplication, and failure resilience

## Task Commits

Each task was committed atomically (TDD: test then feat):

1. **Task 1 RED: ensure_ready() concurrency tests** - `de69f29` (test)
2. **Task 1 GREEN: ensure_ready()/_start() restructuring** - `66ec16c` (feat)
3. **Task 2 RED: invoke_tool() refresh tests** - `ba6ae00` (test)
4. **Task 2 GREEN: invoke_tool() two-lock-cycle refresh** - `8a97935` (feat)

## Files Created/Modified

- `packages/core/mcp_hangar/domain/model/provider.py` - Added _ready_event/threading.Event startup coordination, restructured ensure_ready() into fast/starter/waiter paths, extracted_start() I/O from lock scope, restructured invoke_tool() with multi-lock-cycle refresh pattern, added_refresh_in_progress deduplication flag
- `packages/core/tests/unit/test_provider_aggregate.py` - Added TestEnsureReadyConcurrency (6 tests) and TestInvokeToolRefresh (4 tests) classes

## Decisions Made

- Used threading.Event (not Condition) for startup coordination -- simpler API, clear set/wait semantics, sufficient for single-producer-multiple-consumer pattern
- Multi-lock-cycle pattern for refresh follows health_check() reference implementation: acquire-check-copy, release-I/O, acquire-update
- Simple boolean _refresh_in_progress flag for deduplication rather than per-tool or event-based -- avoids complexity, sufficient because tool catalogs are provider-wide

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Provider lock restructuring complete (CONC-02, CONC-03) -- all Provider I/O now happens outside locks
- Combined with plan 01 (CONC-01, CONC-04), all concurrency requirements for Phase 8 are satisfied
- Ready for plan 03 (exception hygiene audit, EXCP-01)

## Self-Check: PASSED

All files exist. All 4 commits verified.

---
*Phase: 08-safety-foundation*
*Completed: 2026-03-08*
