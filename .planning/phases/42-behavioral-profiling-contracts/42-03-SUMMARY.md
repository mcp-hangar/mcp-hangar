---
phase: 42-behavioral-profiling-contracts
plan: 03
subsystem: enterprise-bootstrap
tags: [behavioral-profiling, bootstrap, facade, conditional-loading, bsl, tdd]

# Dependency graph
requires:
  - phase: 42-behavioral-profiling-contracts
    provides: IBehavioralProfiler Protocol, NullBehavioralProfiler, BehavioralMode enum, NetworkObservation VO (plan 01); BaselineStore SQLite persistence (plan 02)
provides:
  - BehavioralProfiler facade implementing IBehavioralProfiler (enterprise)
  - bootstrap_behavioral() factory creating configured profiler
  - Server bootstrap conditional loading via try/except ImportError
  - ApplicationContext.behavioral_profiler field
  - Fallback to NullBehavioralProfiler when enterprise module absent
affects: [43-network-logging, 44-deviation-detection, 47-license-key]

# Tech tracking
tech-stack:
  added: []
  patterns: [enterprise conditional loading via try/except ImportError for behavioral module, BehavioralProfiler facade delegates to BaselineStore only in LEARNING mode]

key-files:
  created:
    - enterprise/behavioral/profiler.py
    - enterprise/behavioral/bootstrap.py
    - tests/unit/test_bootstrap_behavioral.py
  modified:
    - enterprise/behavioral/__init__.py
    - src/mcp_hangar/server/bootstrap/__init__.py

key-decisions:
  - "BehavioralProfiler only delegates record_observation to BaselineStore in LEARNING mode; DISABLED and ENFORCING are no-ops (deviation detection in Phase 44 handles ENFORCING separately)"
  - "Followed enterprise/auth/bootstrap.py pattern exactly for conditional loading: try/except ImportError with stub function returning NullBehavioralProfiler"
  - "db_path sourced from event_store.path config (shared SQLite path) rather than introducing a separate behavioral.db_path config key"

patterns-established:
  - "Enterprise behavioral bootstrap follows auth bootstrap pattern: try/except ImportError, _enterprise_behavioral_available flag, stub function fallback"
  - "BehavioralProfiler as facade: thin delegation layer coordinating BaselineStore with mode-aware filtering"

requirements-completed: [SC42-4]

# Metrics
duration: 5min
completed: 2026-03-25
---

# Phase 42 Plan 03: Bootstrap Wiring Summary

**BehavioralProfiler facade delegating to BaselineStore in LEARNING mode, wired into server bootstrap via try/except ImportError conditional loading with NullBehavioralProfiler fallback**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-24T23:53:07Z
- **Completed:** 2026-03-24T23:58:20Z
- **Tasks:** 2/2 (TDD: RED + GREEN each)
- **Files modified:** 5

## Accomplishments
- Implemented BehavioralProfiler facade satisfying IBehavioralProfiler Protocol: delegates get_mode/set_mode to BaselineStore, record_observation filtered by LEARNING mode
- Created bootstrap_behavioral() factory function that creates BaselineStore and BehavioralProfiler, logs initialization
- Wired enterprise behavioral module into server bootstrap with try/except ImportError pattern matching existing auth bootstrap
- Added behavioral_profiler field to ApplicationContext dataclass
- NullBehavioralProfiler fallback with INFO log when enterprise module unavailable
- Updated enterprise/behavioral/__init__.py with public API exports
- 14 tests covering Protocol conformance, delegation, mode filtering, bootstrap factory, server wiring, and fallback

## Task Commits

Each task was committed atomically (TDD: RED then GREEN):

1. **Task 1: BehavioralProfiler facade and enterprise bootstrap**
   - `b4daf68` (test) -- RED: 11 failing tests for profiler protocol, delegation, mode filtering, bootstrap factory
   - `e510127` (feat) -- GREEN: implement profiler.py, bootstrap.py, update __init__.py

2. **Task 2: Wire behavioral bootstrap into server bootstrap**
   - `58ec0fd` (test) -- RED: 3 failing tests for ApplicationContext field, bootstrap wiring, enterprise flag
   - `a177fd2` (feat) -- GREEN: add try/except ImportError block, behavioral_profiler field, wiring in bootstrap()

## Files Created/Modified
- `enterprise/behavioral/profiler.py` -- BehavioralProfiler facade implementing IBehavioralProfiler (77 lines)
- `enterprise/behavioral/bootstrap.py` -- bootstrap_behavioral() factory function (47 lines)
- `enterprise/behavioral/__init__.py` -- Updated with public API exports (BaselineStore, BehavioralProfiler, bootstrap_behavioral)
- `src/mcp_hangar/server/bootstrap/__init__.py` -- try/except ImportError block + ApplicationContext.behavioral_profiler field + wiring in bootstrap()
- `tests/unit/test_bootstrap_behavioral.py` -- 14 tests (190 lines)

## Decisions Made
- BehavioralProfiler only records observations in LEARNING mode; ENFORCING mode observation handling deferred to Phase 44 (deviation detection)
- Reused event_store.path as db_path for BaselineStore rather than adding a separate config key (observations and events share the same SQLite file path default)
- Followed enterprise auth bootstrap pattern exactly: same structure, same fallback pattern, same _enterprise_*_available flag naming

## Deviations from Plan

None -- plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None -- no external service configuration required.

## Next Phase Readiness
- Phase 42 is now complete (all 3 plans done): contracts, persistence, and bootstrap wiring
- Phase 43 (Network Connection Logging) can proceed: BehavioralProfiler.record_observation() is the entry point for network monitors
- Phase 44 (Deviation Detection) can proceed: IDeviationDetector contract ready, BaselineStore provides baseline data
- Phase 47 (License Key) can layer on top: bootstrap already conditionally loads enterprise modules

## Self-Check: PASSED

- All 5 created/modified files: FOUND
- All 4 task commits (b4daf68, e510127, 58ec0fd, a177fd2): FOUND

---
*Phase: 42-behavioral-profiling-contracts*
*Completed: 2026-03-25*
