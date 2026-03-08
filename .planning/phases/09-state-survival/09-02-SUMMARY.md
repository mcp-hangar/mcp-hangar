---
phase: 09-state-survival
plan: 02
subsystem: circuit-breaker, snapshots
tags: [circuit-breaker, persistence, serialization, snapshot, state-survival]

# Dependency graph
requires:
  - phase: 08-safety-foundation plan 03
    provides: Exception hygiene with fault-barrier/infra-boundary conventions
provides:
  - CircuitBreaker.from_dict() classmethod for full state restoration from persisted dict
  - ProviderSnapshot.circuit_breaker_state field for CB state persistence
  - Round-trip serialization fidelity for circuit breaker open/closed state
affects: [09-state-survival plan 03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "from_dict/to_dict symmetry: CircuitBreaker.from_dict() mirrors to_dict() for lossless round-trip"
    - "backward-compatible snapshot field: circuit_breaker_state defaults to None for pre-existing snapshots"

key-files:
  created: []
  modified:
    - packages/core/mcp_hangar/domain/model/circuit_breaker.py
    - packages/core/mcp_hangar/domain/model/event_sourced_provider.py
    - packages/core/tests/unit/test_circuit_breaker.py
    - packages/core/tests/integration/test_event_sourced_provider.py

key-decisions:
  - "Added opened_at to CircuitBreaker.to_dict() -- required for from_dict() to restore open breakers with correct timestamp, but was missing from original to_dict() output"
  - "ProviderSnapshot.circuit_breaker_state typed as dict[str, Any] | None -- uses raw dict rather than CircuitBreaker instance to avoid coupling snapshot dataclass to CircuitBreaker lifecycle"
  - "create_snapshot() leaves circuit_breaker_state as None -- CB lives on ProviderGroup not Provider, actual population deferred to plan 09-03 bootstrap wiring"

patterns-established:
  - "Snapshot backward compat: new optional fields default to None so from_dict() handles old snapshots without circuit_breaker_state key"

requirements-completed: [PERS-03]

# Metrics
duration: ~7min
completed: 2026-03-08
---

# Phase 9 Plan 2: Circuit Breaker Persistence Summary

**CircuitBreaker.from_dict() classmethod with opened_at field in to_dict(), ProviderSnapshot extended with backward-compatible circuit_breaker_state field, 11 TDD tests confirming round-trip fidelity**

## Performance

- **Duration:** ~7 min
- **Started:** 2026-03-08T20:04:09Z
- **Completed:** 2026-03-08T20:11:58Z
- **Tasks:** 1 (TDD: RED + GREEN + REFACTOR)
- **Files modified:** 4

## Accomplishments

- Implemented `CircuitBreaker.from_dict()` classmethod that reconstructs full CB state (state, failure_count, opened_at, config) from a persisted dictionary
- Added `opened_at` field to `CircuitBreaker.to_dict()` return dict (was missing, needed for open breaker restoration)
- Extended `ProviderSnapshot` dataclass with `circuit_breaker_state: dict[str, Any] | None = None` field
- Updated `ProviderSnapshot.to_dict()` and `from_dict()` to include circuit_breaker_state with backward-compatible None default
- Wrote 11 TDD tests: 7 for CircuitBreaker (from_dict closed/open, round-trip, missing fields, config restore, opened_at in to_dict) and 4 for ProviderSnapshot (CB state round-trip, backward compat, from_snapshot with/without CB state)
- All 2147 tests pass with no regressions

## Task Commits

Each TDD phase was committed atomically:

1. **RED: Failing tests for circuit breaker persistence** - `51b65ba` (test)
2. **GREEN: Implement circuit breaker persistence** - `048a4c5` (feat)
3. **REFACTOR: Skipped** -- code is clean, follows existing patterns, no cleanup needed

## Files Created/Modified

### packages/core/mcp_hangar/domain/model/circuit_breaker.py

- Added `from typing import Any` import
- Added `opened_at` field to `to_dict()` return dict
- Changed `to_dict()` return type annotation to `dict[str, Any]`
- Added `from_dict(cls, d: dict[str, Any]) -> "CircuitBreaker"` classmethod

### packages/core/mcp_hangar/domain/model/event_sourced_provider.py

- Added `circuit_breaker_state: dict[str, Any] | None = None` field to `ProviderSnapshot` dataclass
- Updated `ProviderSnapshot.to_dict()` to include `circuit_breaker_state`
- Updated `ProviderSnapshot.from_dict()` to read `circuit_breaker_state` from dict

### packages/core/tests/unit/test_circuit_breaker.py

- Added `TestCircuitBreakerFromDict` class with 5 tests:
  - `test_from_dict_closed_state`
  - `test_from_dict_open_state`
  - `test_to_dict_from_dict_round_trip`
  - `test_from_dict_missing_fields_uses_defaults`
  - `test_from_dict_restores_config`
- Added 2 tests to `TestCircuitBreakerToDict`:
  - `test_to_dict_includes_opened_at_none_when_closed`
  - `test_to_dict_includes_opened_at_when_open`

### packages/core/tests/integration/test_event_sourced_provider.py

- Added `TestProviderSnapshotCircuitBreakerState` class with 4 tests:
  - `test_snapshot_circuit_breaker_state_round_trip`
  - `test_snapshot_without_circuit_breaker_state_backward_compat`
  - `test_from_snapshot_with_circuit_breaker_state`
  - `test_from_snapshot_with_none_circuit_breaker_state`

## Decisions Made

- **opened_at added to to_dict():** The existing `to_dict()` omitted `opened_at`, but `from_dict()` needs it to restore open circuit breakers with the correct timestamp. Added to maintain round-trip fidelity.
- **Raw dict for snapshot field:** `circuit_breaker_state` stores the CB's `to_dict()` output as a raw dict rather than a `CircuitBreaker` instance. This avoids coupling the snapshot dataclass to CB lifecycle and lets the consumer decide when to reconstruct.
- **create_snapshot() unchanged:** Circuit breakers live on `ProviderGroup`, not `Provider`. The `create_snapshot()` method correctly defaults `circuit_breaker_state=None`. Actual CB state population during snapshot creation is deferred to plan 09-03 bootstrap wiring.

## Deviations from Plan

None -- plan executed exactly as written.

## Issues Encountered

- Pre-existing test failures in `test_saga_manager.py` and `test_saga_state_store.py` are from plan 09-01's RED phase (failing tests awaiting implementation). Not related to 09-02 changes.
- Pre-existing LSP error on `event_sourced_provider.py` line 246 (ToolSchema type mismatch) is unrelated.

## User Setup Required

None -- no external service configuration required.

## Next Phase Readiness

- Plan 09-02 provides the persistence primitives (from_dict/to_dict, snapshot field) that plan 09-03 will wire into bootstrap
- Plan 09-03 (Idempotency filter + bootstrap wiring) is the final plan in Phase 9 and depends on both 09-01 (saga persistence) and 09-02 (CB persistence)

## Self-Check: PASSED

All 4 modified files verified present. Both task commits (51b65ba, 048a4c5) verified in git log. All 11 new tests pass. No regressions in 2147-test suite.

---
*Phase: 09-state-survival*
*Completed: 2026-03-08*
