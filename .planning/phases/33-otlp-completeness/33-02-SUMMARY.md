---
phase: 33-otlp-completeness
plan: 02
subsystem: observability
tags: [otlp, otel, audit, event-handler, domain-events]

# Dependency graph
requires:
  - phase: 33-01
    provides: "IAuditExporter port, NullAuditExporter, OTLPAuditExporter"
provides:
  - "OTLPAuditEventHandler bridges domain events to IAuditExporter"
  - "Bootstrap wiring: OTLP audit handler registered on ToolInvocationCompleted/Failed + ProviderStateChanged"
affects: ["33-03 (OTEL Collector recipe)", "enterprise compliance exporter"]

# Tech tracking
tech-stack:
  added: []
  patterns: ["OTLPAuditEventHandler: targeted event subscription for OTLP export"]

key-files:
  created:
    - "src/mcp_hangar/application/event_handlers/audit_event_handler.py"
    - "tests/unit/test_audit_event_handler.py"
  modified:
    - "src/mcp_hangar/server/bootstrap/event_handlers.py"

key-decisions:
  - "Named class OTLPAuditEventHandler to avoid collision with existing AuditEventHandler (audit_handler.py)"
  - "Used targeted subscribe() for 3 specific event types instead of subscribe_to_all for efficiency"
  - "Maps ProviderStateChanged.old_state/new_state to IAuditExporter from_state/to_state parameters"

patterns-established:
  - "OTLPAuditEventHandler: domain event -> IAuditExporter bridge with typed event dispatch"
  - "Targeted event subscription: subscribe() per event type when handler only cares about specific events"

requirements-completed: []

# Metrics
duration: 4min
completed: 2026-03-24
---

# Phase 33 Plan 02: OTLPAuditEventHandler Wiring Summary

**OTLPAuditEventHandler bridges ToolInvocationCompleted/Failed and ProviderStateChanged domain events to IAuditExporter, registered in bootstrap with OTLP endpoint detection**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-24T14:54:35Z
- **Completed:** 2026-03-24T14:58:45Z
- **Tasks:** 2 (TDD RED + GREEN)
- **Files modified:** 3

## Accomplishments
- OTLPAuditEventHandler created with typed dispatch for 3 domain event types
- Bootstrap registers handler with OTLPAuditExporter when OTEL_EXPORTER_OTLP_ENDPOINT env var set, NullAuditExporter otherwise
- 4 unit tests covering success/error tool invocations, state changes, and null exporter fallback
- All 2652 existing tests pass (no regressions)

## Task Commits

Each task was committed atomically:

1. **Task 1: RED -- Write failing tests for OTLPAuditEventHandler** - `19ecf85` (test)
2. **Task 2: GREEN -- Implement OTLPAuditEventHandler + register in bootstrap** - `df84216` (feat)

## Files Created/Modified
- `src/mcp_hangar/application/event_handlers/audit_event_handler.py` - OTLPAuditEventHandler: dispatches ToolInvocationCompleted/Failed/ProviderStateChanged to IAuditExporter
- `src/mcp_hangar/server/bootstrap/event_handlers.py` - Registers OTLPAuditEventHandler with targeted event subscriptions and OTLP endpoint detection
- `tests/unit/test_audit_event_handler.py` - 4 unit tests for event handler wiring

## Decisions Made
- Named class `OTLPAuditEventHandler` (not `AuditEventHandler`) to avoid collision with existing `AuditEventHandler` in `audit_handler.py` which handles in-memory/log audit store
- Used `subscribe()` per event type (ToolInvocationCompleted, ToolInvocationFailed, ProviderStateChanged) instead of `subscribe_to_all()` -- more efficient since handler only cares about 3 event types
- Mapped `ProviderStateChanged.old_state`/`new_state` fields to `IAuditExporter.export_provider_state_change(from_state=, to_state=)` -- the domain event uses `old_state`/`new_state` while the exporter port uses `from_state`/`to_state`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed event field names to match actual dataclass definitions**
- **Found during:** Task 1 (RED -- writing tests)
- **Issue:** Plan assumed `ProviderStateChanged(from_state=, to_state=)` but actual fields are `old_state`/`new_state`. Plan assumed `ToolInvocationFailed(error=)` but actual field is `error_message`. Plan assumed no `correlation_id` on tool events but both require it.
- **Fix:** Adjusted test constructors and handler implementation to use actual domain event field names
- **Files modified:** `tests/unit/test_audit_event_handler.py`, `src/mcp_hangar/application/event_handlers/audit_event_handler.py`
- **Verification:** All 4 tests pass with correct field mappings
- **Committed in:** `19ecf85` (test), `df84216` (feat)

**2. [Rule 3 - Blocking] Renamed class to OTLPAuditEventHandler to avoid name collision**
- **Found during:** Task 1 (RED -- writing tests)
- **Issue:** Plan specified class name `AuditEventHandler` but that name already exists in `audit_handler.py` and is exported from the event_handlers package `__init__.py`
- **Fix:** Used `OTLPAuditEventHandler` to distinguish from existing `AuditEventHandler`
- **Files modified:** `tests/unit/test_audit_event_handler.py`, `src/mcp_hangar/application/event_handlers/audit_event_handler.py`, `src/mcp_hangar/server/bootstrap/event_handlers.py`
- **Verification:** No import conflicts, all tests pass
- **Committed in:** `19ecf85` (test), `df84216` (feat)

---

**Total deviations:** 2 auto-fixed (1 bug, 1 blocking)
**Impact on plan:** Both fixes necessary for correctness. Class name change avoids import collision. Field name alignment ensures handler correctly reads domain event data. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- OTLPAuditEventHandler wired and tested, ready for Plan 33-03 (OTEL Collector docker-compose recipe)
- End-to-end flow: domain events -> OTLPAuditEventHandler -> OTLPAuditExporter -> OTLP log records
- NullAuditExporter used by default, OTLPAuditExporter activated by OTEL_EXPORTER_OTLP_ENDPOINT env var

---
*Phase: 33-otlp-completeness*
*Completed: 2026-03-24*

## Self-Check: PASSED

- All 3 key files exist on disk
- Both task commits (19ecf85, df84216) found in git history
