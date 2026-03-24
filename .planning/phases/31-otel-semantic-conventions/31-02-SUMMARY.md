---
phase: 31-otel-semantic-conventions
plan: 02
subsystem: observability
tags: [opentelemetry, tracing, traced-provider-service, governance-telemetry]

# Dependency graph
requires:
  - phase: 31-otel-semantic-conventions-01
    provides: set_governance_attributes() helper and convention constants (Provider.ID, MCP.TOOL_NAME, MCP.TOOL_STATUS)
provides:
  - OTEL span creation on every TracedProviderService.invoke_tool() call
  - Governance attributes (provider.id, tool.name, tool.status, user.id, session.id) on OTEL spans
  - Dual tracing: OTEL governance span runs in parallel with ObservabilityPort (Langfuse) span
affects: [31-03-PLAN, 32-trace-context-propagation, 33-otlp-completeness]

# Tech tracking
tech-stack:
  added: []
  patterns: [dual-span-tracing-otel-plus-observability-port]

key-files:
  created:
    - tests/unit/test_traced_provider_service_otel.py
  modified:
    - src/mcp_hangar/application/services/traced_provider_service.py

key-decisions:
  - "OTEL span wraps entire invoke_tool body including ObservabilityPort span -- both run in parallel, neither replaces the other"
  - "set_governance_attributes called before invocation for early attribute visibility; MCP.TOOL_STATUS set after invocation for result"

patterns-established:
  - "Dual span tracing: OTEL governance span for OTLP backends + ObservabilityPort span for LLM observability (Langfuse) -- both coexist per invocation"

requirements-completed: []

# Metrics
duration: 2min
completed: 2026-03-24
---

# Phase 31 Plan 02: Add OTEL Span to TracedProviderService Summary

**TracedProviderService.invoke_tool() now creates a direct OTEL span with governance attributes (provider.id, tool.name, tool.status, user.id, session.id) in parallel with the existing ObservabilityPort/Langfuse span**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-24T13:39:11Z
- **Completed:** 2026-03-24T13:41:50Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Added OTEL span to TracedProviderService.invoke_tool() via get_tracer() and set_governance_attributes()
- Span carries Provider.ID, MCP.TOOL_NAME, MCP.TOOL_STATUS on every invocation; user_id and session_id when provided
- Error path records exception on span and sets TOOL_STATUS to "error"
- 5 new unit tests covering span creation, governance attributes, success/error status, and user/session attributes

## Task Commits

Each task was committed atomically:

1. **Task 1: RED -- Write failing tests for OTEL span** - `02899c1` (test)
2. **Task 2: GREEN -- Add OTEL span to TracedProviderService.invoke_tool** - `a18bb42` (feat)

## Files Created/Modified
- `tests/unit/test_traced_provider_service_otel.py` - 5 tests validating OTEL span creation and governance attributes on TracedProviderService
- `src/mcp_hangar/application/services/traced_provider_service.py` - Added get_tracer/set_governance_attributes imports; invoke_tool wraps all work in OTEL span

## Decisions Made
- OTEL span wraps the entire invoke_tool body (including ObservabilityPort span) so both trace paths coexist per invocation
- set_governance_attributes() called at span start for early attribute visibility; MCP.TOOL_STATUS set after invocation outcome is known

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Ready for 31-03-PLAN.md (InMemorySpanExporter integration test validating real OTEL SDK spans)
- TracedProviderService now creates OTEL spans that can be captured by InMemorySpanExporter in the integration test

---
*Phase: 31-otel-semantic-conventions*
*Completed: 2026-03-24*

## Self-Check: PASSED

- All 2 key files exist on disk
- Both task commits verified in git history (02899c1, a18bb42)
- 28 OTEL-related tests pass (23 from Plan 01 + 5 from Plan 02)
