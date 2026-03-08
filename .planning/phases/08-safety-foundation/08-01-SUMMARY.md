---
phase: 08-safety-foundation
plan: 01
subsystem: concurrency, security
tags: [threading, lock-hierarchy, input-validation, stdio, discovery, provider-group]

# Dependency graph
requires: []
provides:
  - StdioClient ordering invariant documented with regression tests (CONC-04)
  - Discovery pipeline command validation via InputValidator (SECR-01)
  - ProviderGroup two-phase lock pattern eliminating lock hierarchy violation (CONC-01)
affects: [08-02, 08-03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Two-phase lock pattern: snapshot refs under lock, do I/O outside lock, re-acquire to update state"
    - "TrackedLock ownership check via get_current_thread_locks() not _is_owned()"

key-files:
  created:
    - packages/core/tests/unit/test_stdio_client_ordering.py
    - packages/core/tests/unit/test_discovery_command_validation.py
  modified:
    - packages/core/mcp_hangar/stdio_client.py
    - packages/core/mcp_hangar/application/discovery/discovery_orchestrator.py
    - packages/core/mcp_hangar/server/bootstrap/discovery.py
    - packages/core/mcp_hangar/domain/model/provider_group.py
    - packages/core/tests/unit/test_provider_group.py

key-decisions:
  - "Used get_current_thread_locks() from lock_hierarchy module to verify TrackedLock ownership in tests (TrackedLock has no _is_owned())"
  - "Two-phase lock pattern for ProviderGroup: Phase 1 lock/snapshot, Phase 2 unlock/I-O, Phase 3 re-lock/update"
  - "InputValidator injected as optional dependency into DiscoveryOrchestrator with TYPE_CHECKING guard"

patterns-established:
  - "Two-phase lock: When a higher-level lock (group level 11) must call a method that acquires a lower-level lock (provider level 10), snapshot references under the higher lock, release it, do I/O, then re-acquire to update state"
  - "Race condition guard: After re-acquiring lock in Phase 3, always check if the member still exists before updating"

requirements-completed: [CONC-04, SECR-01, CONC-01]

# Metrics
duration: 11min
completed: 2026-03-08
---

# Phase 08 Plan 01: Three Independent Safety Fixes Summary

**StdioClient ordering invariant documented with tests, discovery command validation wired in, and ProviderGroup lock hierarchy violation fixed via two-phase lock pattern**

## Performance

- **Duration:** 11 min
- **Started:** 2026-03-08T18:29:51Z
- **Completed:** 2026-03-08T18:40:48Z
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments

- Documented StdioClient's register-before-write ordering invariant with safety comment block and 2 regression tests
- Wired InputValidator.validate_command() into DiscoveryOrchestrator._process_provider() to reject dangerous commands before registration
- Fixed ProviderGroup lock hierarchy violation (CONC-01) by restructuring add_member(), start_all(), and stop_all() with two-phase lock pattern

## Task Commits

Each task was committed atomically:

1. **Task 1: Document StdioClient ordering invariant** - `f08f252` (feat)
2. **Task 2: Wire command validation into discovery** - `55682c2` (test/RED), `9bc8bed` (feat/GREEN)
3. **Task 3: Fix ProviderGroup lock hierarchy** - `d9d8793` (test/RED), `8a026e0` (fix/GREEN)

## Files Created/Modified

- `packages/core/mcp_hangar/stdio_client.py` - Added safety comment block documenting register-before-write invariant
- `packages/core/tests/unit/test_stdio_client_ordering.py` - 2 regression tests for ordering invariant
- `packages/core/mcp_hangar/application/discovery/discovery_orchestrator.py` - Added InputValidator injection and command validation in _process_provider()
- `packages/core/mcp_hangar/server/bootstrap/discovery.py` - Wired InputValidator into orchestrator bootstrap
- `packages/core/tests/unit/test_discovery_command_validation.py` - 5 TDD behavior tests for discovery command validation
- `packages/core/mcp_hangar/domain/model/provider_group.py` - Restructured add_member(), start_all(), stop_all() with two-phase lock pattern; replaced_try_start_member() with _try_start_member_unlocked()
- `packages/core/tests/unit/test_provider_group.py` - 5 lock hierarchy tests (3 lock-not-held assertions, 1 race condition edge case, 1 concurrent deadlock check)

## Decisions Made

- Used `get_current_thread_locks()` from `infrastructure.lock_hierarchy` to verify TrackedLock ownership in tests, since TrackedLock wraps RLock but does not expose `_is_owned()`
- Implemented two-phase lock pattern for all ProviderGroup methods that call Provider I/O (ensure_ready, shutdown): snapshot refs under lock, release lock for I/O, re-acquire to update state
- InputValidator injected as `InputValidator | None = None` with `from __future__ import annotations` and `TYPE_CHECKING` guard to avoid circular imports

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] TrackedLock lacks _is_owned() method**

- **Found during:** Task 3 (TDD RED phase)
- **Issue:** Plan's test design used `group._lock._is_owned()` but TrackedLock wraps RLock and does not expose this method
- **Fix:** Replaced with `get_current_thread_locks()` from `infrastructure.lock_hierarchy` module, checking for `LockLevel.PROVIDER_GROUP` in thread-local held locks list
- **Files modified:** `packages/core/tests/unit/test_provider_group.py`
- **Verification:** All 5 lock hierarchy tests pass; 3 correctly fail in RED phase (lock IS held), all 5 pass in GREEN phase
- **Committed in:** `d9d8793` (RED commit)

---

**Total deviations:** 1 auto-fixed (1 blocking issue)
**Impact on plan:** Necessary adaptation to TrackedLock API. No scope creep.

## Issues Encountered

- Pre-existing LSP errors throughout the codebase (subprocess pipe types, docker client types) are not caused by our changes and were ignored
- Pre-existing ruff warning about unused variable in `test_provider_aggregate.py` line 364 was avoided by not staging that file

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- All 3 safety requirements (CONC-04, SECR-01, CONC-01) are resolved
- Two-phase lock pattern established as the standard for ProviderGroup; future code touching group I/O should follow this pattern
- Plan 08-02 (ensure_ready threading.Event coordination) can proceed independently

---
*Phase: 08-safety-foundation*
*Completed: 2026-03-08*

## Self-Check: PASSED

All 8 files verified present. All 5 task commits verified in git log.
