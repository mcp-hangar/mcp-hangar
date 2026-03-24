---
phase: 42-behavioral-profiling-contracts
plan: 01
subsystem: domain
tags: [behavioral-profiling, protocol, value-objects, domain-events, tdd]

# Dependency graph
requires:
  - phase: 37-ci-license-docs
    provides: enterprise/ directory structure and import boundary enforcement
provides:
  - IBehavioralProfiler, IBaselineStore, IDeviationDetector @runtime_checkable Protocol contracts
  - BehavioralMode enum (LEARNING/ENFORCING/DISABLED)
  - NetworkObservation frozen dataclass with validation
  - BehavioralModeChanged domain event
  - NullBehavioralProfiler no-op implementation
affects: [42-02, 42-03, 43-network-logging, 44-deviation-detection, 45-schema-drift, 47-license-key]

# Tech tracking
tech-stack:
  added: []
  patterns: [runtime_checkable Protocol for enterprise contract boundary, frozen dataclass value objects with validation]

key-files:
  created:
    - src/mcp_hangar/domain/contracts/behavioral.py
    - src/mcp_hangar/domain/value_objects/behavioral.py
    - tests/unit/domain/test_behavioral_contracts.py
  modified:
    - src/mcp_hangar/domain/contracts/__init__.py
    - src/mcp_hangar/domain/value_objects/__init__.py
    - src/mcp_hangar/domain/events.py

key-decisions:
  - "Used forward string references in Protocol signatures to avoid circular imports between contracts and value_objects"
  - "NullBehavioralProfiler imports BehavioralMode inside method body to avoid circular dependency at module level"
  - "NetworkObservation uses tuple-style frozen dataclass following EgressRule pattern from capabilities.py"

patterns-established:
  - "Behavioral contracts follow authentication.py Protocol pattern: @runtime_checkable + @abstractmethod"
  - "Value objects follow capabilities.py pattern: enum with __str__ + frozen dataclass with __post_init__ validation"

requirements-completed: [SC42-1, SC42-3]

# Metrics
duration: 3min
completed: 2026-03-25
---

# Phase 42 Plan 01: Behavioral Profiling Contracts Summary

**Three @runtime_checkable Protocol contracts (IBehavioralProfiler, IBaselineStore, IDeviationDetector), BehavioralMode enum, NetworkObservation value object, BehavioralModeChanged domain event, and NullBehavioralProfiler no-op -- all MIT-licensed in domain layer**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-24T23:41:34Z
- **Completed:** 2026-03-24T23:45:04Z
- **Tasks:** 2/2
- **Files modified:** 6

## Accomplishments
- Defined 3 @runtime_checkable Protocol contracts for behavioral profiling: IBehavioralProfiler (facade), IBaselineStore (persistence), IDeviationDetector (analysis)
- Created BehavioralMode enum (LEARNING/ENFORCING/DISABLED) and NetworkObservation frozen dataclass with input validation (empty host/provider_id, port range 0-65535)
- Added BehavioralModeChanged domain event inheriting from DomainEvent with schema_version=1
- Implemented NullBehavioralProfiler satisfying IBehavioralProfiler Protocol (returns DISABLED for all providers)
- All types re-exported from __init__.py modules for clean public API
- 26 unit tests covering all contracts, value objects, domain event, and null implementation

## Task Commits

Each task was committed atomically (TDD: RED then GREEN):

1. **Task 1: Define behavioral value objects and domain event**
   - `3c85444` (test) -- RED: failing tests for BehavioralMode, NetworkObservation, BehavioralModeChanged
   - `7d81231` (feat) -- GREEN: implement value objects and domain event
2. **Task 2: Define behavioral contracts with NullBehavioralProfiler**
   - `4618096` (test) -- RED: failing tests for contracts and NullBehavioralProfiler
   - `bcc0bbd` (feat) -- GREEN: implement contracts and null implementation

## Files Created/Modified
- `src/mcp_hangar/domain/contracts/behavioral.py` -- IBehavioralProfiler, IBaselineStore, IDeviationDetector Protocols + NullBehavioralProfiler
- `src/mcp_hangar/domain/value_objects/behavioral.py` -- BehavioralMode enum, NetworkObservation frozen dataclass
- `src/mcp_hangar/domain/events.py` -- Added BehavioralModeChanged domain event
- `src/mcp_hangar/domain/contracts/__init__.py` -- Re-exports for behavioral contracts
- `src/mcp_hangar/domain/value_objects/__init__.py` -- Re-exports for behavioral value objects
- `tests/unit/domain/test_behavioral_contracts.py` -- 26 unit tests

## Decisions Made
- Used forward string references ("BehavioralMode", "NetworkObservation") in Protocol method signatures to avoid circular imports between contracts/ and value_objects/ packages
- NullBehavioralProfiler imports BehavioralMode inside get_mode() method body rather than at module level to prevent circular dependency
- Followed existing project patterns exactly: authentication.py for Protocol pattern, capabilities.py for enum + frozen dataclass pattern, events.py for DomainEvent pattern

## Deviations from Plan

None -- plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None -- no external service configuration required.

## Next Phase Readiness
- MIT contracts are stable targets for Plans 02 and 03 (BSL implementations)
- Plan 42-02 can proceed: IBehavioralProfiler and IBaselineStore contracts ready for SQLite-backed BaselineStore implementation in enterprise/behavioral/
- Plan 42-03 can proceed: NullBehavioralProfiler pattern established for bootstrap wiring
- Phases 43-46 have concrete Protocol interfaces to implement against

## Self-Check: PASSED

- All 4 created/modified source files: FOUND
- All 4 task commits (3c85444, 7d81231, 4618096, bcc0bbd): FOUND

---
*Phase: 42-behavioral-profiling-contracts*
*Completed: 2026-03-25*
