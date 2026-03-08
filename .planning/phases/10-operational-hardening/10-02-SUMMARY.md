---
phase: 10-operational-hardening
plan: 02
subsystem: database
tags: [event-sourcing, snapshots, sqlite, persistence, aggregate-replay]

requires:
  - phase: 09-state-survival
    provides: "SQLiteEventStore, EventSourcedProviderRepository, ProviderSnapshot with CB state"
provides:
  - "IEventStore save_snapshot()/load_snapshot() abstract contract"
  - "SQLiteEventStore snapshots table with version-consistent save under lock"
  - "InMemoryEventStore (persistence) in-memory snapshot storage"
  - "NullEventStore no-op snapshot implementations"
  - "EventSourcedProviderRepository dual-API compatibility (old EventStore + new IEventStore)"
  - "Aggregate replay from latest snapshot plus subsequent events"
affects: [10-operational-hardening, event-sourcing, provider-lifecycle]

tech-stack:
  added: []
  patterns:
    - "hasattr-based API detection for backward compatibility across event store versions"
    - "INSERT OR REPLACE for snapshot upsert (latest-wins semantics)"
    - "Dual-path hydration: skip for new API (DomainEvent), hydrate for old API (StoredEvent)"

key-files:
  created:
    - packages/core/tests/unit/test_event_sourced_repository.py
  modified:
    - packages/core/mcp_hangar/domain/contracts/event_store.py
    - packages/core/mcp_hangar/infrastructure/persistence/sqlite_event_store.py
    - packages/core/mcp_hangar/infrastructure/persistence/in_memory_event_store.py
    - packages/core/mcp_hangar/infrastructure/event_sourced_repository.py
    - packages/core/tests/unit/test_event_store_persistence.py

key-decisions:
  - "hasattr-based API detection at __init__ for old/new event store compatibility (self._has_new_api, self._has_snapshot_methods)"
  - "Dual hydration path: new IEventStore.read_stream() returns DomainEvent directly, old EventStore.load() returns StoredEvent needing hydration"
  - "InMemoryEventStore (persistence module) also gets snapshot support for test symmetry"

patterns-established:
  - "API compatibility bridge: detect capabilities once at init via hasattr, branch at call sites"
  - "Snapshot-accelerated replay: load snapshot, read events from snapshot_version+1, apply only delta"

requirements-completed: [PERS-04, PERS-05]

duration: 8min
completed: 2026-03-08
---

# Phase 10 Plan 02: Event Store Snapshots Summary

**IEventStore snapshot contract with SQLiteEventStore persistence, dual-API repository bridge, and snapshot-accelerated aggregate replay**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-03-08
- **Completed:** 2026-03-08
- **Tasks:** 2 (TDD: 4 commits total -- 2 RED + 2 GREEN)
- **Files modified:** 6

## Accomplishments

- Added `save_snapshot()` and `load_snapshot()` abstract methods to `IEventStore` contract with `NullEventStore` no-op implementations
- Implemented SQLiteEventStore snapshot persistence with `snapshots` table, version-consistent save under `_lock`, and `INSERT OR REPLACE` upsert semantics
- Rewired `EventSourcedProviderRepository` with dual-API compatibility bridge supporting both old `EventStore` and new `IEventStore` APIs via `hasattr`-based detection
- Aggregate replay now loads from latest snapshot + subsequent events, bounding startup time regardless of total event history

## Task Commits

Each task was committed atomically (TDD RED then GREEN):

1. **Task 1: Add snapshot methods to IEventStore and SQLiteEventStore**
   - `e32e64d` (test) -- RED: failing tests for snapshot methods
   - `9315963` (feat) -- GREEN: implement snapshot support in contract + stores
2. **Task 2: Wire repository to use IEventStore snapshots**
   - `2facf79` (test) -- RED: failing tests for repository snapshot integration
   - `77c0021` (feat) -- GREEN: dual-API bridge + snapshot-accelerated replay

## Files Created/Modified

- `packages/core/mcp_hangar/domain/contracts/event_store.py` -- Added `save_snapshot()` and `load_snapshot()` abstract methods to `IEventStore`, no-op implementations in `NullEventStore`
- `packages/core/mcp_hangar/infrastructure/persistence/sqlite_event_store.py` -- Added `snapshots` table to `_init_schema()`, implemented `save_snapshot()` (under `_lock`) and `load_snapshot()`
- `packages/core/mcp_hangar/infrastructure/persistence/in_memory_event_store.py` -- Added `_snapshots` dict with thread-safe `save_snapshot()`/`load_snapshot()`
- `packages/core/mcp_hangar/infrastructure/event_sourced_repository.py` -- Major update: API compatibility detection, 4 helper methods, dual-path snapshot load/save, dual-path event hydration
- `packages/core/tests/unit/test_event_store_persistence.py` -- Added snapshot round-trip, overwrite, None-return, and NullEventStore tests
- `packages/core/tests/unit/test_event_sourced_repository.py` -- New file: 6 tests covering snapshot save/load via IEventStore, state equivalence, backward compatibility

## Decisions Made

- **hasattr-based API detection**: The repository detects old vs new event store API once at `__init__` (`self._has_new_api`, `self._has_snapshot_methods`) and branches at call sites. This avoids modifying the old `EventStore` class while enabling seamless use of the new `IEventStore`.
- **Dual hydration path**: New `IEventStore.read_stream()` returns `DomainEvent` objects directly (no hydration needed), while old `EventStore.load()` returns `StoredEvent` objects requiring `_hydrate_events()`. The repository branches on `self._has_new_api`.
- **InMemoryEventStore gets snapshots too**: Added snapshot support to the persistence module's `InMemoryEventStore` for test symmetry and completeness, not just `SQLiteEventStore`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added compatibility helpers for dual event store APIs**

- **Found during:** Task 2
- **Issue:** The repository was typed against the old `EventStore` API (`load()`, `get_version()`, `stream_exists()`, `get_all_stream_ids()`) but needs to work with the new `IEventStore` API (`read_stream()`, `get_stream_version()`, `list_streams()`). Direct method calls would crash at runtime.
- **Fix:** Added 4 private helper methods (`_store_get_version`, `_store_stream_exists`, `_store_load_events`, `_store_get_all_stream_ids`) that branch on `self._has_new_api` detected at init time. Updated all repository methods (`add`, `exists`, `get_all`, `get_all_ids`, `get_event_history`, `replay_provider`) to use these helpers.
- **Files modified:** `packages/core/mcp_hangar/infrastructure/event_sourced_repository.py`
- **Verification:** All 6 repository tests pass; full regression (2243 tests) clean
- **Committed in:** `77c0021`

**2. [Rule 1 - Bug] Fixed test data using uppercase state values**

- **Found during:** Task 2
- **Issue:** Test events used `"COLD"` and `"INITIALIZING"` but `ProviderState` enum uses lowercase values (`"cold"`, `"initializing"`), causing `from_events()` to fail with `ValueError`
- **Fix:** Changed all test event state values to lowercase
- **Files modified:** `packages/core/tests/unit/test_event_sourced_repository.py`
- **Committed in:** `77c0021`

**3. [Rule 1 - Bug] Fixed snapshot state equivalence test**

- **Found during:** Task 2
- **Issue:** `_record_event()` does NOT apply events (no state mutation, no version increment). Providers created only with `_record_event()` have un-applied state, so snapshots don't reflect expected state. The equivalence test was comparing empty/initial state.
- **Fix:** Used `from_events()` first (which calls `_apply_event()`) to build a provider with correct state, then recorded additional events on top for snapshot interval testing
- **Files modified:** `packages/core/tests/unit/test_event_sourced_repository.py`
- **Committed in:** `77c0021`

---

**Total deviations:** 3 auto-fixed (2 bugs, 1 blocking)
**Impact on plan:** All auto-fixes necessary for correctness. The dual-API bridge was the most significant addition -- plan assumed a single API but reality has two event store layers coexisting.

## Issues Encountered

None beyond the deviations documented above.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Snapshot infrastructure complete -- aggregate replay is now bounded
- Ready for 10-03 (rate limiter command bus middleware)
- The dual-API bridge pattern established here can be referenced if other components need to work across old/new event store APIs

---
*Phase: 10-operational-hardening*
*Completed: 2026-03-08*
