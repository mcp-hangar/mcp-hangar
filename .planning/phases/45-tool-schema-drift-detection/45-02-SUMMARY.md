---
phase: 45-tool-schema-drift-detection
plan: 02
subsystem: behavioral
tags: [schema-drift, domain-events, prometheus, otlp, event-handler]

# Dependency graph
requires:
  - phase: 45-01
    provides: "SchemaTracker with check_and_store() and bootstrap_schema_tracker factory"
  - phase: 44
    provides: "BehavioralDeviationDetected event pattern, BehavioralDeviationEventHandler pattern"
provides:
  - "SchemaChangeType enum (ADDED/REMOVED/MODIFIED) for classifying tool schema changes"
  - "ToolSchemaChanged domain event with per-tool granularity"
  - "Provider.get_tool_schemas() public accessor"
  - "TOOL_SCHEMA_DRIFTS_TOTAL Prometheus counter with record_tool_schema_drift()"
  - "ToolSchemaChangeHandler bridging ProviderStarted to SchemaTracker"
affects: [45-03, enterprise-behavioral]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Event handler bridge pattern (ProviderStarted -> enterprise check -> domain event)"
    - "Fault-isolated handler with broad exception catch at boundary"

key-files:
  created:
    - "src/mcp_hangar/application/event_handlers/tool_schema_change_handler.py"
  modified:
    - "src/mcp_hangar/domain/value_objects/behavioral.py"
    - "src/mcp_hangar/domain/events.py"
    - "src/mcp_hangar/domain/model/provider.py"
    - "src/mcp_hangar/metrics.py"
    - "src/mcp_hangar/server/bootstrap/__init__.py"

key-decisions:
  - "ToolSchemaChanged event uses string change_type (not enum) for serialization compatibility"
  - "Handler registered inline in bootstrap after schema_tracker creation (not in init_event_handlers)"
  - "One OTLP span per changed tool (not per provider) for fine-grained observability"

patterns-established:
  - "Event handler bridge: MIT handler calls enterprise component via DI, publishes MIT events"
  - "Bootstrap late-registration: handlers needing enterprise components register after conditional import"

requirements-completed: [SC45-1, SC45-2, SC45-3]

# Metrics
duration: 4min
completed: 2026-03-25
---

# Phase 45 Plan 02: MIT Domain Types + Event Handler Summary

**SchemaChangeType enum, ToolSchemaChanged per-tool event, Provider.get_tool_schemas() accessor, Prometheus counter, and ToolSchemaChangeHandler bridge wired into bootstrap**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-25T10:11:14Z
- **Completed:** 2026-03-25T10:15:59Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- SchemaChangeType enum (ADDED/REMOVED/MODIFIED) in MIT domain layer for classifying tool schema changes
- ToolSchemaChanged domain event with per-tool granularity (one event per changed tool, not per provider)
- Provider.get_tool_schemas() thread-safe public accessor returning list[ToolSchema]
- TOOL_SCHEMA_DRIFTS_TOTAL Prometheus counter with [provider, change_type] labels
- ToolSchemaChangeHandler bridging ProviderStarted -> SchemaTracker.check_and_store() -> ToolSchemaChanged events with OTLP spans and Prometheus metrics
- Bootstrap wiring registers handler on ProviderStarted after schema_tracker creation

## Task Commits

Each task was committed atomically:

1. **Task 1: Add SchemaChangeType enum, ToolSchemaChanged event, Provider.get_tool_schemas(), Prometheus counter** - `015a373` (feat)
2. **Task 2: Create ToolSchemaChangeHandler and wire into bootstrap** - `065a44c` (feat)

## Files Created/Modified
- `src/mcp_hangar/domain/value_objects/behavioral.py` - Added SchemaChangeType enum
- `src/mcp_hangar/domain/events.py` - Added ToolSchemaChanged domain event
- `src/mcp_hangar/domain/model/provider.py` - Added get_tool_schemas() public method
- `src/mcp_hangar/metrics.py` - Added TOOL_SCHEMA_DRIFTS_TOTAL counter and record_tool_schema_drift()
- `src/mcp_hangar/application/event_handlers/tool_schema_change_handler.py` - New handler bridging ProviderStarted to schema drift detection
- `src/mcp_hangar/server/bootstrap/__init__.py` - Handler registration after schema_tracker creation

## Decisions Made
- ToolSchemaChanged event uses string change_type field (not enum) for serialization compatibility, matching BehavioralDeviationDetected pattern
- Handler registered inline in bootstrap() after schema_tracker creation rather than in init_event_handlers(), because schema_tracker is created later in the bootstrap flow
- One OTLP span per changed tool for fine-grained observability rather than one span per provider

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Ready for 45-03 (unit tests for the event handler and integration verification)
- All MIT domain types are in place for the schema drift pipeline
- Enterprise SchemaTracker (45-01) + MIT handler (45-02) form the complete event emission pipeline

## Self-Check: PASSED

All 6 key files verified on disk. Both commit hashes (015a373, 065a44c) found in git log.

---
*Phase: 45-tool-schema-drift-detection*
*Completed: 2026-03-25*
