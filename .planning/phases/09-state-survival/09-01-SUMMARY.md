---
phase: 09-state-survival
plan: 01
subsystem: saga-persistence, infrastructure
tags: [saga, persistence, sqlite, serialization, state-survival, checkpoint, tdd]

# Dependency graph
requires:
  - phase: 08-safety-foundation plan 03
    provides: Exception hygiene with fault-barrier conventions for checkpoint error handling
provides:
  - SagaStateStore with checkpoint/load/mark_processed/is_processed backed by SQLite
  - NullSagaStateStore null object for backward compatibility
  - to_dict/from_dict serialization on all EventTriggeredSaga subclasses
  - SagaManager checkpoint integration after successful saga.handle() calls
affects: [09-state-survival plan 03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Saga checkpoint outside lock: SagaManager checkpoints state after handle() outside its TrackedLock, following no-I/O-under-locks rule"
    - "Fault-barrier checkpoint: checkpoint failure logged but does not prevent event handling or command dispatch"
    - "Null object persistence: NullSagaStateStore injected when no SQLite store configured"

key-files:
  created:
    - packages/core/mcp_hangar/infrastructure/persistence/saga_state_store.py
  modified:
    - packages/core/mcp_hangar/infrastructure/saga_manager.py
    - packages/core/mcp_hangar/application/sagas/provider_recovery_saga.py
    - packages/core/mcp_hangar/application/sagas/provider_failover_saga.py
    - packages/core/mcp_hangar/application/sagas/group_rebalance_saga.py
    - packages/core/tests/unit/test_saga_state_store.py
    - packages/core/tests/unit/test_saga_manager.py

key-decisions:
  - "Circular import resolved by importing mcp_hangar.application.commands before application.sagas submodules in tests"
  - "TrackedLock ownership verified via _get_held_locks() thread-local tracking instead of acquire(blocking=False) which does not work with RLock from same thread"
  - "Checkpoint fires after saga.handle() but before command dispatch -- captures post-handle saga state regardless of command execution outcome"

patterns-established:
  - "Saga serialization contract: all EventTriggeredSaga subclasses implement to_dict()/from_dict() for state round-trip"
  - "Checkpoint-outside-lock pattern: snapshot saga list under lock, iterate and checkpoint outside lock"

requirements-completed: [PERS-01]

# Metrics
duration: ~16min
completed: 2026-03-08
---

# Phase 9 Plan 1: Saga Persistence Foundation Summary

**SagaStateStore backed by SQLite with MigrationRunner-managed schema, to_dict/from_dict serialization on all three concrete sagas, and SagaManager checkpoint integration with fault-barrier protection outside lock scope**

## Performance

- **Duration:** ~16 min
- **Started:** 2026-03-08T20:04:10Z
- **Completed:** 2026-03-08T20:19:41Z
- **Tasks:** 2 (TDD: 4 total commits across 2 RED-GREEN cycles)
- **Files modified:** 7 (1 new, 6 modified)

## Accomplishments

- Created `SagaStateStore` with `checkpoint()`, `load()`, `mark_processed()`, `is_processed()` methods backed by SQLite via existing `SQLiteConnectionFactory` and `MigrationRunner` infrastructure
- Created `NullSagaStateStore` null object for backward compatibility when no persistence is configured
- Added abstract `to_dict()`/`from_dict()` methods to `EventTriggeredSaga` base class, with concrete implementations on `ProviderRecoverySaga` (retry state), `ProviderFailoverSaga` (failover configs, active failovers, active backups, pending failbacks), and `GroupRebalanceSaga` (no-op, stateless)
- Integrated checkpoint writes into `SagaManager._handle_event()` outside the TrackedLock with fault-barrier protection
- All 2189 tests pass with no regressions

## Task Commits

Each TDD phase was committed atomically:

1. **Task 1 RED: Failing tests for SagaStateStore and saga serialization** - `db4834e` (test)
2. **Task 1 GREEN: Implement SagaStateStore and saga serialization** - `1bb335f` (feat)
3. **Task 2 RED: Failing tests for SagaManager checkpoint integration** - `e104dc0` (test)
4. **Task 2 GREEN: Integrate checkpoint into SagaManager._handle_event** - `586209b` (feat)

## Files Created/Modified

### packages/core/mcp_hangar/infrastructure/persistence/saga_state_store.py (NEW)

- `SagaStateStore` class: SQLite-backed persistence with `checkpoint()`, `load()`, `mark_processed()`, `is_processed()`
- `NullSagaStateStore` class: null object pattern, all methods return None/False
- `SAGA_STORE_MIGRATIONS`: version 1 migration creating `saga_state` and `saga_idempotency` tables
- Uses `SQLiteConnectionFactory` and `MigrationRunner` from `database_common.py`

### packages/core/mcp_hangar/infrastructure/saga_manager.py

- Added abstract `to_dict()` and `from_dict()` methods to `EventTriggeredSaga`
- Added optional `saga_state_store` parameter to `SagaManager.__init__()`
- Modified `_handle_event()` to checkpoint saga state after successful `handle()` calls, outside lock, with fault-barrier try/except

### packages/core/mcp_hangar/application/sagas/provider_recovery_saga.py

- Added `to_dict()`: serializes `_retry_state` dict
- Added `from_dict()`: restores `_retry_state` from persisted data

### packages/core/mcp_hangar/application/sagas/provider_failover_saga.py

- Added `to_dict()`: serializes `_failover_configs`, `_active_failovers`, `_active_backups`, `_pending_failbacks`
- Added `from_dict()`: reconstructs `FailoverConfig` and `FailoverState` dataclasses, restores `_active_backups` as set

### packages/core/mcp_hangar/application/sagas/group_rebalance_saga.py

- Added `to_dict()`: returns empty dict (stateless saga)
- Added `from_dict()`: no-op (state comes from group objects)

### packages/core/tests/unit/test_saga_state_store.py

- 19 tests covering: checkpoint persistence, load retrieval, overwrite behavior, unknown saga type, mark_processed/is_processed, round-trip serialization for all three saga types, migration table creation

### packages/core/tests/unit/test_saga_manager.py

- 5 new checkpoint tests added: checkpoint called after handle(), no checkpoint when store is None, checkpoint outside lock verification, fault-barrier on checkpoint failure, correct arguments passed to checkpoint

## Decisions Made

- **Circular import workaround:** Test files import `mcp_hangar.application.commands` before `application.sagas` submodules to ensure the circular import chain (`sagas.__init__` -> `group_rebalance_saga` -> `commands` -> `reload_handler` -> `server.config` -> `server.state` -> `application.sagas`) completes before individual saga modules are imported.
- **Lock verification approach:** Used `_get_held_locks()` from `lock_hierarchy.py` to verify checkpoint happens outside lock, rather than `acquire(blocking=False)` which is unreliable with reentrant locks from the same thread.
- **Checkpoint timing:** Checkpoint fires after `saga.handle()` but before command dispatch, so saga state is persisted even if downstream command execution fails.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Circular import in test setup**

- **Found during:** Task 1 (RED phase)
- **Issue:** Importing saga submodules triggered circular import chain through `application.sagas.__init__` -> `server.state` -> `application.sagas`
- **Fix:** Added `from mcp_hangar.application.commands import Command  # noqa: F401` before saga imports in test files to pre-populate the import chain
- **Files modified:** `test_saga_state_store.py`, `test_saga_manager.py`
- **Verification:** All imports resolve cleanly, tests run without ImportError
- **Committed in:** `db4834e`, `e104dc0`

**2. [Rule 1 - Bug] MagicMock recursive call in spy pattern**

- **Found during:** Task 2 (RED phase)
- **Issue:** Using `original = mock.method; original()` inside a `side_effect` caused infinite recursion because `original` IS the mock object
- **Fix:** Rewrote spy to just track call state without calling original mock method
- **Files modified:** `test_saga_manager.py`
- **Verification:** Spy tests pass without recursion errors
- **Committed in:** `e104dc0`

---

**Total deviations:** 2 auto-fixed (1 blocking import, 1 bug fix)
**Impact on plan:** Both fixes necessary for test execution. No scope creep.

## Issues Encountered

- Pre-existing LSP errors in multiple files (gc.py, rate_limiter.py, docker_source.py, lock_hierarchy.py, stdio_client.py) are unrelated to 09-01 changes.
- Unstaged changes from plan 09-02 (`circuit_breaker.py`, `event_sourced_provider.py`) present in working tree at times during execution -- carefully excluded from 09-01 commits by staging files individually.

## User Setup Required

None -- no external service configuration required.

## Next Phase Readiness

- Plan 09-01 provides the saga persistence infrastructure (SagaStateStore, serialization, checkpoint integration) that plan 09-03 will wire into bootstrap for saga resume on startup
- Plan 09-03 (Idempotency filter + bootstrap wiring) is the final plan in Phase 9 and depends on both 09-01 (saga persistence) and 09-02 (CB persistence)
- The `mark_processed()`/`is_processed()` idempotency methods on SagaStateStore are ready for plan 09-03 to integrate

## Self-Check: PASSED

All 7 source/test files verified present. All 4 task commits (db4834e, 1bb335f, e104dc0, 586209b) verified in git log. 19 saga store tests + 23 saga manager tests pass. No regressions in 2189-test suite.

---
*Phase: 09-state-survival*
*Completed: 2026-03-08*
