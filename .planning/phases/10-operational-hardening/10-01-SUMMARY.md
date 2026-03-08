---
phase: 10-operational-hardening
plan: 01
subsystem: infra
tags: [health-check, backoff, jitter, scheduling, thundering-herd]

# Dependency graph
requires:
  - phase: 09-operational-hardening
    provides: "Provider aggregate with HealthTracker and BackgroundWorker"
provides:
  - "HealthTracker with jittered exponential backoff (_calculate_backoff with random.uniform)"
  - "HealthTracker.get_health_check_interval(state) for state-aware intervals"
  - "BackgroundWorker with per-provider _next_check_at scheduling"
  - "State-aware health check: skip COLD/INITIALIZING, normal for READY, backoff for DEGRADED, ceiling for DEAD"
affects: [10-operational-hardening]

# Tech tracking
tech-stack:
  added: []
  patterns: [jittered-backoff, per-provider-scheduling, state-aware-health-checks]

key-files:
  created:
    - packages/core/tests/unit/test_background_worker.py
  modified:
    - packages/core/mcp_hangar/domain/model/health_tracker.py
    - packages/core/mcp_hangar/gc.py
    - packages/core/tests/unit/test_health_tracker.py

key-decisions:
  - "jitter_factor default 0.1 (10%) -- same pattern as retry.py calculate_backoff()"
  - "get_health_check_interval returns 0.0 for COLD/INITIALIZING (skip), normal for READY, backoff for DEGRADED, 60s ceiling for DEAD"
  - "BackgroundWorker keeps time.sleep(interval_s) as base tick rate with per-provider _next_check_at timestamps for skip logic"
  - "Stale _next_check_at entries cleaned up after each loop iteration for removed providers"

patterns-established:
  - "Jittered backoff: base * random.uniform(-factor, +factor) clamped to [0, ceiling]"
  - "State-aware scheduling: per-provider timestamps with skip/normal/backoff/ceiling intervals"

requirements-completed: [RESL-01, RESL-02]

# Metrics
duration: 8min
completed: 2026-03-08
---

# Phase 10 Plan 01: Health Check Jitter and State-Aware Scheduling Summary

**Jittered exponential backoff in HealthTracker and per-provider state-aware health check scheduling in BackgroundWorker to prevent thundering herd**

## Performance

- **Duration:** 8 min
- **Started:** 2026-03-08
- **Completed:** 2026-03-08
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- HealthTracker._calculate_backoff() now applies random jitter (configurable, default 10%) to prevent thundering herd when multiple degraded providers retry simultaneously
- New get_health_check_interval(state, normal_interval) method returns state-appropriate check intervals (0.0 for skip, normal for READY, backoff for DEGRADED, 60s ceiling for DEAD)
- BackgroundWorker uses per-provider _next_check_at timestamps instead of global fixed interval, skipping COLD/INITIALIZING providers entirely
- 37 new/updated tests (25 health tracker + 12 background worker) with 100% pass rate

## Task Commits

Each task was committed atomically (TDD: RED then GREEN):

1. **Task 1 RED: Add failing tests for HealthTracker jitter** - `ee214e6` (test)
2. **Task 1 GREEN: Implement jitter in HealthTracker** - `49c2065` (feat)
3. **Task 2 RED: Add failing tests for BackgroundWorker scheduling** - `06281c2` (test)
4. **Task 2 GREEN: Implement state-aware BackgroundWorker scheduling** - `d4f8327` (feat)

## Files Created/Modified

- `packages/core/mcp_hangar/domain/model/health_tracker.py` - Added jitter_factor field, jittered_calculate_backoff(), get_health_check_interval() method
- `packages/core/mcp_hangar/gc.py` - Added _next_check_at dict, state-aware health check branch (skip/normal/backoff/ceiling), stale entry cleanup
- `packages/core/tests/unit/test_health_tracker.py` - Added TestHealthTrackerJitter class with 12 tests (jitter range, randomness, deterministic, state intervals)
- `packages/core/tests/unit/test_background_worker.py` - New file with 12 tests (8 unit + 4 integration for state-aware scheduling)

## Decisions Made

- Used same jitter pattern as retry.py: base * random.uniform(-factor, +factor) -- consistency across codebase
- jitter_factor=0.0 produces deterministic backoff (useful for testing)
- BackgroundWorker keeps time.sleep(interval_s) as base tick rate -- per-provider timestamps control actual check frequency within each tick
- get_health_check_interval accepts string state (not enum) for loose coupling with normalize_state_to_str()

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Adjusted tolerance in existing test_time_until_retry_after_failure**

- **Found during:** Task 1 (HealthTracker jitter implementation)
- **Issue:** Existing test asserted backoff <= 2.0 but jitter_factor=0.1 can push 2^1 backoff to 2.2
- **Fix:** Changed tolerance from <= 2.0 to <= 2.2
- **Files modified:** packages/core/tests/unit/test_health_tracker.py
- **Verification:** Test passes with jittered backoff values
- **Committed in:** ee214e6 (Task 1 RED commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix)
**Impact on plan:** Minimal -- existing test needed tolerance adjustment for jitter. No scope creep.

## Issues Encountered

- ProviderState is Enum (not StrEnum as documented in plan interfaces) -- required using normalize_state_to_str() throughout, which was already the established pattern
- Pre-existing test failure in test_event_sourced_repository.py from 10-02 plan stubs -- excluded from regression concerns (not caused by our changes)

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- HealthTracker jitter and state-aware intervals ready for use by all provider health checks
- BackgroundWorker scheduling foundation in place for further operational hardening plans
- No blockers for subsequent plans (10-02 through 10-06)

---
*Phase: 10-operational-hardening*
*Completed: 2026-03-08*
