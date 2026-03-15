---
phase: 21-log-capture-infrastructure
plan: 01
subsystem: infrastructure
tags: [log-capture, ring-buffer, domain-events, thread-safety, python]

# Dependency graph
requires: []
provides:
  - LogLine frozen dataclass (domain/value_objects/log.py)
  - IProviderLogBuffer ABC contract (domain/contracts/log_buffer.py)
  - ProviderLogBuffer deque-backed ring buffer with threading.Lock (infrastructure/persistence/log_buffer.py)
  - Singleton registry: get_log_buffer, set_log_buffer, get_or_create_log_buffer, remove_log_buffer, clear_log_buffer_registry
  - init_log_buffers() bootstrap wiring (server/bootstrap/logs.py)
affects: [21-02, 21-03, 22-01, 22-02, 22-03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Ring buffer using collections.deque(maxlen=N) for O(1) append with automatic eviction
    - Module-level singleton registry with _registry_lock for thread-safe idempotent creation
    - on_append callback wired OUTSIDE lock to avoid I/O-under-lock antipattern
    - Lazy imports in init_log_buffers() to avoid circular dependency between bootstrap sub-modules

key-files:
  created:
    - packages/core/mcp_hangar/domain/value_objects/log.py
    - packages/core/mcp_hangar/domain/contracts/log_buffer.py
    - packages/core/mcp_hangar/infrastructure/persistence/log_buffer.py
    - packages/core/mcp_hangar/server/bootstrap/logs.py
    - packages/core/tests/unit/test_log_buffer.py
    - packages/core/tests/unit/test_bootstrap_logs.py
  modified:
    - packages/core/mcp_hangar/domain/value_objects/__init__.py
    - packages/core/mcp_hangar/domain/contracts/__init__.py
    - packages/core/mcp_hangar/infrastructure/persistence/__init__.py

key-decisions:
  - "DEFAULT_MAX_LINES = 1000 per provider -- configurable at construction time"
  - "on_append callback invoked outside the lock to prevent I/O blocking buffer writers"
  - "get_or_create_log_buffer protected by _registry_lock for thread-safe idempotency"
  - "Lazy imports in init_log_buffers to break circular dependency between server and infrastructure layers"

patterns-established:
  - "Pattern 1: Ring buffer via deque(maxlen=N) -- O(1) append, automatic oldest-entry eviction"
  - "Pattern 2: Module-level registry with Lock -- thread-safe singleton pattern for per-provider objects"
  - "Pattern 3: on_append callback outside lock -- safe async notification without blocking buffer"

requirements-completed: [LOG-01]

# Metrics
duration: 1min
completed: 2026-03-15
---

# Phase 21 Plan 01: Log Capture Infrastructure Foundation Summary

**LogLine frozen dataclass, IProviderLogBuffer ABC, ProviderLogBuffer deque ring-buffer with thread-safe singleton registry, and init_log_buffers() bootstrap wiring -- foundation for all Phase 21/22 log capture components**

## Performance

- **Duration:** 1 min
- **Started:** 2026-03-15T10:00:03Z
- **Completed:** 2026-03-15T10:01:16Z
- **Tasks:** 2 (verification tasks -- code was pre-implemented)
- **Files modified:** 9

## Accomplishments

- Verified LogLine frozen dataclass with all four fields (provider_id, stream, content, recorded_at) and to_dict() serialization
- Verified IProviderLogBuffer ABC with append, tail, clear, and provider_id abstractmethods
- Verified ProviderLogBuffer using deque(maxlen) with threading.Lock -- ring semantics confirmed with 39 passing unit tests
- Verified singleton registry (get_log_buffer, set_log_buffer, get_or_create_log_buffer, remove_log_buffer, clear_log_buffer_registry) with thread-safe idempotent creation
- Verified init_log_buffers() creates ProviderLogBuffer per provider, wires broadcaster.notify as on_append, registers in singleton, and injects into Provider aggregate

## Task Commits

Each task was committed atomically (pre-implemented, committed before plan execution):

1. **Task 1: Verify LogLine value object and IProviderLogBuffer contract** - `d11ba6f` (feat -- pre-implemented)
2. **Task 2: Verify ProviderLogBuffer ring buffer, singleton registry, and bootstrap wiring** - `d11ba6f` (feat -- pre-implemented)

**Plan metadata:** _(this summary commit -- docs: complete plan)_

_Note: Both tasks verified pre-implemented code. The implementation commit d11ba6f contains all files._

## Files Created/Modified

- `packages/core/mcp_hangar/domain/value_objects/log.py` - LogLine frozen dataclass
- `packages/core/mcp_hangar/domain/contracts/log_buffer.py` - IProviderLogBuffer ABC with thread-safety docstring
- `packages/core/mcp_hangar/infrastructure/persistence/log_buffer.py` - ProviderLogBuffer + singleton registry (all 5 functions)
- `packages/core/mcp_hangar/server/bootstrap/logs.py` - init_log_buffers() bootstrap wiring
- `packages/core/mcp_hangar/domain/value_objects/__init__.py` - Re-exports LogLine
- `packages/core/mcp_hangar/domain/contracts/__init__.py` - Re-exports IProviderLogBuffer
- `packages/core/mcp_hangar/infrastructure/persistence/__init__.py` - Re-exports all log_buffer symbols
- `packages/core/tests/unit/test_log_buffer.py` - 31 unit tests for LogLine, ProviderLogBuffer, registry
- `packages/core/tests/unit/test_bootstrap_logs.py` - 8 integration tests for init_log_buffers()

## Decisions Made

- DEFAULT_MAX_LINES = 1000 -- covers most provider output without excessive memory use
- on_append callback invoked outside the lock -- prevents I/O under lock antipattern per CLAUDE.md rules
- get_or_create_log_buffer uses _registry_lock for thread-safe idempotency
- Lazy imports in init_log_buffers() avoid circular dependency between server/bootstrap and infrastructure/persistence layers

## Deviations from Plan

None - plan executed exactly as written. All pre-implemented files matched specifications exactly. All 39 unit tests passed on first run.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Self-Check

- [x] `packages/core/mcp_hangar/domain/value_objects/log.py` - FOUND
- [x] `packages/core/mcp_hangar/domain/contracts/log_buffer.py` - FOUND
- [x] `packages/core/mcp_hangar/infrastructure/persistence/log_buffer.py` - FOUND
- [x] `packages/core/mcp_hangar/server/bootstrap/logs.py` - FOUND
- [x] `packages/core/tests/unit/test_log_buffer.py` - FOUND
- [x] `packages/core/tests/unit/test_bootstrap_logs.py` - FOUND
- [x] Commit `d11ba6f` exists in git log
- [x] All 39 tests passed

## Self-Check: PASSED

## Next Phase Readiness

- Foundation complete: LogLine, IProviderLogBuffer, ProviderLogBuffer, registry, and bootstrap wiring all verified
- Ready for Plan 21-02 (stderr reader threads, Provider log_buffer injection via launchers)
- Ready for Plan 21-03 (GET /api/providers/{id}/logs REST endpoint)

---
_Phase: 21-log-capture-infrastructure_
_Completed: 2026-03-15_
