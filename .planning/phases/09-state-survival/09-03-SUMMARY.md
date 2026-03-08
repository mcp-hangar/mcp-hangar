---
phase: 09-state-survival
plan: 03
subsystem: saga-persistence, bootstrap
tags: [idempotency, saga, circuit-breaker, bootstrap, state-survival, sqlite]

# Dependency graph
requires:
  - phase: 09-state-survival plan 01
    provides: SagaStateStore with checkpoint/load/is_processed/mark_processed, saga to_dict/from_dict
  - phase: 09-state-survival plan 02
    provides: CircuitBreaker.from_dict() classmethod, ProviderSnapshot.circuit_breaker_state field
provides:
  - Idempotent SagaManager event handling via is_processed() check before saga.handle()
  - Bootstrap wiring that creates SagaStateStore, loads persisted saga state, restores group CB state
  - Shutdown persistence of circuit breaker state via save_group_circuit_breakers()
  - saga_state_store field on ApplicationContext for lifecycle access
affects: [10-operational-hardening]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Idempotency guard: check is_processed() before saga.handle(), mark_processed() after checkpoint"
    - "Bootstrap state restore: _restore_saga_state() loads persisted state, _restore_group_circuit_breakers() loads CB state"
    - "Shutdown persistence: save_group_circuit_breakers() called in ApplicationContext.shutdown() before stopping providers"
    - "Saga state store as general-purpose small-state persistence: CB state stored under saga_type=circuit_breaker"

key-files:
  created: []
  modified:
    - packages/core/mcp_hangar/infrastructure/saga_manager.py
    - packages/core/mcp_hangar/server/bootstrap/cqrs.py
    - packages/core/mcp_hangar/server/bootstrap/__init__.py
    - packages/core/tests/unit/test_saga_manager.py
    - packages/core/tests/unit/test_saga_state_store.py

key-decisions:
  - "Idempotency check skipped for events without global_position -- live events do not need replay protection, only persisted event replay"
  - "mark_processed() placed inside existing checkpoint fault-barrier block -- a single try/except covers checkpoint + mark_processed, keeping error handling simple"
  - "Saga state store reused for circuit breaker persistence under saga_type=circuit_breaker -- avoids new tables or persistence mechanisms for small CB state"
  - "CB state saved at shutdown only (not on every state change) -- minimally viable persistence that avoids write amplification during normal operation"
  - "init_saga() returns SagaStateStore to caller so ApplicationContext can reference it for shutdown persistence"

patterns-established:
  - "Idempotency guard pattern: is_processed() before handle(), mark_processed() after checkpoint, skip when no global_position"
  - "Bootstrap restore pattern: load() from store, call from_dict() if result exists, log and continue if not (first boot)"

requirements-completed: [PERS-02, PERS-03]

# Metrics
duration: 9min
completed: 2026-03-08
---

# Phase 9 Plan 3: Idempotency Filter + Bootstrap Wiring Summary

**SagaManager idempotency filter prevents duplicate commands on event replay, bootstrap creates SagaStateStore and restores saga + circuit breaker state from SQLite, shutdown persists CB state for groups**

## Performance

- **Duration:** ~9 min
- **Started:** 2026-03-08T20:24:38Z
- **Completed:** 2026-03-08T20:33:44Z
- **Tasks:** 2 (both TDD: RED + GREEN)
- **Files modified:** 5

## Accomplishments

- Implemented idempotency filter in `SagaManager._handle_event()` that checks `is_processed()` before `saga.handle()` and calls `mark_processed()` after checkpoint, preventing duplicate command emission during event replay
- Expanded `init_saga()` in bootstrap `cqrs.py` to create `SagaStateStore` (SQLite-backed when configured), restore persisted state for recovery and failover sagas, restore circuit breaker state for provider groups, and inject the store into `SagaManager`
- Added `saga_state_store` field to `ApplicationContext` dataclass and `save_group_circuit_breakers()` call in `shutdown()` for CB persistence across restarts
- Wrote 14 TDD tests: 6 for SagaManager idempotency filter + 8 for bootstrap wiring (store creation, saga restoration, CB restore/save)
- All 2203 tests pass with no regressions

## Task Commits

Each TDD phase was committed atomically:

1. **Task 1 RED: Failing tests for SagaManager idempotency filter** - `7d7ef78` (test)
2. **Task 1 GREEN: Implement idempotency filter in SagaManager._handle_event** - `db5af62` (feat)
3. **Task 2 RED: Failing tests for bootstrap saga wiring and CB restore** - `49982a3` (test)
4. **Task 2 GREEN: Implement bootstrap saga wiring and CB persistence** - `5ba63bc` (feat)

## Files Created/Modified

### packages/core/mcp_hangar/infrastructure/saga_manager.py

- Added idempotency check before `saga.handle()` in `_handle_event()`: checks `is_processed()` when both `_saga_state_store` and `global_position` are available
- Added `mark_processed()` call inside existing checkpoint fault-barrier block after `checkpoint()`
- Events without `global_position` bypass idempotency check (live events vs replayed events)

### packages/core/mcp_hangar/server/bootstrap/cqrs.py

- Added `_create_saga_state_store(full_config)`: creates `SagaStateStore` with `SQLiteConnectionFactory` when event_store driver is "sqlite", otherwise returns `NullSagaStateStore`
- Added `_restore_saga_state(store, saga)`: loads persisted state via `store.load()` and calls `saga.from_dict()` to restore
- Added `_restore_group_circuit_breakers(store, groups)`: loads CB state under `saga_type="circuit_breaker"` and replaces group's `_circuit_breaker` via `CircuitBreaker.from_dict()`
- Added `save_group_circuit_breakers(store, groups)`: persists each group's CB state via `store.checkpoint()` under `saga_type="circuit_breaker"`
- Expanded `init_saga(full_config)` to orchestrate all of the above and return the store instance
- Added imports for `CircuitBreaker`, `NullSagaStateStore`, `SagaStateStore`, `ProviderRecoverySaga`, `ProviderFailoverSaga`

### packages/core/mcp_hangar/server/bootstrap/**init**.py

- Added `saga_state_store: SagaStateStore | NullSagaStateStore | None = None` field to `ApplicationContext`
- Added `save_group_circuit_breakers()` call in `ApplicationContext.shutdown()` before stopping providers (with fault-barrier)
- Changed `init_saga()` call to `init_saga(full_config)` and captured return value
- Passed `saga_state_store` to `ApplicationContext` constructor
- Added imports for `save_group_circuit_breakers`, `NullSagaStateStore`, `SagaStateStore`

### packages/core/tests/unit/test_saga_manager.py

- Added `TestSagaManagerIdempotency` class with 6 tests:
  - `test_skips_already_processed_event`
  - `test_processes_event_when_not_already_processed`
  - `test_skips_idempotency_check_when_no_global_position`
  - `test_no_idempotency_check_when_store_is_none`
  - `test_mark_processed_called_with_correct_args`
  - `test_mark_processed_failure_does_not_break_handling`
- Fixed existing `test_checkpoint_passes_correct_saga_state_and_position` to set `mock_store.is_processed.return_value = False`

### packages/core/tests/unit/test_saga_state_store.py

- Added `TestBootstrapSagaWiring` class with 8 tests:
  - `test_init_saga_creates_saga_state_store_with_sqlite`
  - `test_init_saga_uses_null_store_when_no_event_store`
  - `test_init_saga_uses_null_store_when_memory_driver`
  - `test_restore_saga_state_loads_recovery_saga`
  - `test_restore_saga_state_loads_failover_saga`
  - `test_restore_saga_state_handles_missing_state`
  - `test_restore_group_circuit_breakers`
  - `test_save_group_circuit_breakers`

## Decisions Made

- **Idempotency check placement:** Before `saga.handle()` to prevent duplicate commands. `mark_processed()` after `checkpoint()` inside the same fault-barrier. If persistence fails, the event will be reprocessed on next restart (at-least-once, but idempotent).
- **Skip idempotency for live events:** Events without `global_position` (live during normal operation) bypass the check. Only replayed events (with position from event store) need deduplication.
- **Saga state store for CB persistence:** Reused `SagaStateStore.checkpoint()` with `saga_type="circuit_breaker"` and `saga_id=group_id` rather than creating a separate CB persistence mechanism.
- **CB save at shutdown only:** Saves CB state in `ApplicationContext.shutdown()` before stopping providers. Avoids write amplification from saving on every failure event.
- **init_saga() returns store:** Returns `SagaStateStore | NullSagaStateStore` so bootstrap can pass it to `ApplicationContext` for shutdown access.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed existing checkpoint test with is_processed mock**

- **Found during:** Task 1 (idempotency filter implementation)
- **Issue:** `test_checkpoint_passes_correct_saga_state_and_position` used `MagicMock(spec=SagaStateStore)` which returns a truthy MagicMock for `is_processed()` by default, causing the new idempotency check to skip processing
- **Fix:** Added `mock_store.is_processed.return_value = False` to the test
- **Files modified:** `packages/core/tests/unit/test_saga_manager.py`
- **Verification:** Test passes correctly
- **Committed in:** `db5af62` (Task 1 GREEN commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix in test setup)
**Impact on plan:** Minimal. Test mock needed updating to work with the new idempotency check. No scope creep.

## Issues Encountered

- Import ordering in `cqrs.py` required ruff auto-fix (isort) on first commit attempt. Pre-commit hook handled it automatically.
- Circular import in sagas required importing `mcp_hangar.application.commands` before saga submodules in tests (same pattern as established in plan 09-01).

## User Setup Required

None -- no external service configuration required.

## Next Phase Readiness

- Phase 9 (State Survival) is now fully complete: all 3 plans executed
- Saga persistence, circuit breaker persistence, idempotency guards, and bootstrap wiring are all in place
- Phase 10 (Operational Hardening) can proceed: snapshots build on persistence patterns, testing and typing exercise hardened code

## Self-Check: PASSED

All 6 files verified present. All 4 task commits (7d7ef78, db5af62, 49982a3, 5ba63bc) verified in git log. All 2203 tests pass. No regressions.

---
*Phase: 09-state-survival*
*Completed: 2026-03-08*
