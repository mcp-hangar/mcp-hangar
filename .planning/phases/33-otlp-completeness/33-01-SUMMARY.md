---
phase: 33-otlp-completeness
plan: 01
subsystem: observability
tags: [otlp, otel, audit, logs, security-events]

# Dependency graph
requires:
  - phase: 31-otel-semantic-conventions
    provides: "conventions.py attribute constants (MCP, Provider, Enforcement classes)"
provides:
  - "IAuditExporter port in application/ports/observability.py"
  - "NullAuditExporter no-op implementation for disabled OTLP"
  - "OTLPAuditExporter infrastructure adapter exporting tool invocations and state changes as OTLP log records"
affects: ["33-02 (AuditEventHandler wiring)", "33-03 (OTEL Collector recipe)", "enterprise compliance exporter"]

# Tech tracking
tech-stack:
  added: ["opentelemetry._logs (OTEL logs bridge API)"]
  patterns: ["IAuditExporter port/adapter with fault-barrier", "OTLP log record export for governance events"]

key-files:
  created:
    - "src/mcp_hangar/infrastructure/observability/otlp_audit_exporter.py"
    - "tests/unit/test_otlp_audit_exporter.py"
  modified:
    - "src/mcp_hangar/application/ports/observability.py"

key-decisions:
  - "Used Protocol (structural subtyping) for IAuditExporter rather than ABC -- lighter-weight, consistent with other ports in codebase"
  - "Fault-barrier pattern: export failures logged at WARNING and swallowed, never propagated to event handlers"
  - "OTEL logs API imported with try/except -- graceful degradation to structlog when opentelemetry not installed"
  - "Named structlog keyword audit_event (not event) to avoid collision with structlog's internal event parameter"

patterns-established:
  - "IAuditExporter port: all security-relevant event export goes through this interface"
  - "Fault-barrier for observability: try/except around all export calls, log warning on failure"

requirements-completed: []

# Metrics
duration: 7min
completed: 2026-03-24
---

# Phase 33 Plan 01: OTLP Audit Exporter Summary

**IAuditExporter port + OTLPAuditExporter infrastructure adapter exporting tool invocations and provider state changes as OTLP log records with MCP governance attributes**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-24T14:42:59Z
- **Completed:** 2026-03-24T14:50:54Z
- **Tasks:** 2 (TDD RED + GREEN)
- **Files modified:** 3

## Accomplishments
- IAuditExporter protocol and NullAuditExporter no-op defined in application/ports/observability.py
- OTLPAuditExporter exports tool invocation events with provider_id, tool_name, status, duration_ms, optional user_id/session_id/error_type
- OTLPAuditExporter exports provider state change events with provider_id, to_state, previous_state
- Fault-barrier pattern ensures export failures never crash event handlers
- 5 unit tests covering success export, error type inclusion, state change export, fault-barrier, and null exporter

## Task Commits

Each task was committed atomically:

1. **Task 1: RED -- Define IAuditExporter port and write failing tests** - `bcbd968` (test)
2. **Task 2: GREEN -- Implement OTLPAuditExporter** - `3e03b77` (feat)

## Files Created/Modified
- `src/mcp_hangar/application/ports/observability.py` - Added IAuditExporter protocol and NullAuditExporter no-op
- `src/mcp_hangar/infrastructure/observability/otlp_audit_exporter.py` - OTLPAuditExporter with OTLP log record export and fault-barrier
- `tests/unit/test_otlp_audit_exporter.py` - 5 unit tests for audit exporter behavior

## Decisions Made
- Used Protocol (structural subtyping) for IAuditExporter rather than ABC -- lighter-weight, consistent with codebase patterns
- Fault-barrier pattern: export failures logged at WARNING and swallowed, never propagated to event handlers
- OTEL logs API imported with try/except -- graceful degradation to structlog when opentelemetry not installed
- Named structlog keyword `audit_event` (not `event`) to avoid collision with structlog's internal event parameter

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed structlog keyword collision with `event` parameter**
- **Found during:** Task 2 (GREEN -- implement OTLPAuditExporter)
- **Issue:** Plan's `logger.warning()` calls used `event="tool_invocation"` keyword, but structlog uses `event` as the log message (first positional). This caused `TypeError: meth() got multiple values for argument 'event'` when the fault-barrier tried to log an export failure.
- **Fix:** Renamed keyword from `event=` to `audit_event=` in both `logger.warning()` calls
- **Files modified:** `src/mcp_hangar/infrastructure/observability/otlp_audit_exporter.py`
- **Verification:** `test_export_failure_does_not_raise` passes -- fault-barrier correctly swallows exception and logs warning
- **Committed in:** `3e03b77` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Minor naming fix for structlog compatibility. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- IAuditExporter port ready for Plan 33-02 (AuditEventHandler that subscribes to domain events and delegates to IAuditExporter)
- OTLPAuditExporter ready for bootstrap wiring when OTEL_EXPORTER_OTLP_ENDPOINT is set
- NullAuditExporter ready as default when OTLP export is not configured

---
*Phase: 33-otlp-completeness*
*Completed: 2026-03-24*

## Self-Check: PASSED

- All 3 key files exist on disk
- Both task commits (bcbd968, 3e03b77) found in git history
