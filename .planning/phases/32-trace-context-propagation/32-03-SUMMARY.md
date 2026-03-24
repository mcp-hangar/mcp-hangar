---
phase: 32-trace-context-propagation
plan: 03
subsystem: observability
tags: [otel, tracing, w3c-tracecontext, batch-executor, distributed-tracing, integration-test, in-memory-span-exporter]

# Dependency graph
requires:
  - phase: 32-01
    provides: "extract_trace_context wired into BatchExecutor; CallSpec.metadata field"
  - phase: 32-02
    provides: "inject_trace_context wired into HttpClient.call() for outbound headers"
provides:
  - "End-to-end trace propagation integration test validating agent -> BatchExecutor span hierarchy"
  - "batch.call.{tool} span creation in BatchExecutor._execute_call using extracted parent_context"
  - "Phase 32 acceptance test: full W3C TraceContext propagation verified"
affects: [33-otlp-completeness]

# Tech tracking
tech-stack:
  added: []
  patterns: ["InMemorySpanExporter-based integration testing for OTEL spans", "Isolated TracerProvider per test to avoid OTEL set_tracer_provider once-only limitation"]

key-files:
  created:
    - tests/integration/test_trace_propagation_e2e.py
  modified:
    - src/mcp_hangar/server/tools/batch/executor.py

key-decisions:
  - "Added batch.call.{tool} span in _execute_call wrapping the full call lifecycle using extracted parent_context"
  - "Extracted _execute_call_inner to keep span lifecycle separate from trace context extraction"
  - "Used per-test TracerProvider with patched get_tracer instead of global set_tracer_provider to avoid OTEL once-only limitation"

patterns-established:
  - "OTEL integration test pattern: create isolated TracerProvider + InMemorySpanExporter per test, patch get_tracer in the module under test"

requirements-completed: []

# Metrics
duration: 7min
completed: 2026-03-24
---

# Phase 32 Plan 03: End-to-End Trace Propagation Integration Test Summary

**InMemorySpanExporter integration test validates agent traceparent -> BatchExecutor child span with matching trace_id and parent_span_id, completing Phase 32 W3C TraceContext propagation**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-24T14:20:04Z
- **Completed:** 2026-03-24T14:26:50Z
- **Tasks:** 1
- **Files modified:** 2

## Accomplishments
- Created end-to-end integration test proving agent traceparent produces a correlated child span in BatchExecutor
- Added `batch.call.{tool}` span creation in `_execute_call` using the extracted `parent_context` from Plan 32-01
- Verified root span creation when no traceparent provided (graceful degradation, no crash)
- Full Phase 32 test suite (8 tests across 3 plans) passes green

## Task Commits

Each task was committed atomically:

1. **Task 1: Write end-to-end trace propagation integration test** - `b70d292` (test)

## Files Created/Modified
- `tests/integration/test_trace_propagation_e2e.py` - 2 integration tests: child span with traceparent, root span without traceparent
- `src/mcp_hangar/server/tools/batch/executor.py` - Added batch.call.{tool} span in _execute_call; extracted _execute_call_inner

## Decisions Made
- **Span creation in executor**: The extracted `parent_context` from Plan 32-01 was not being used to create any spans. Added `batch.call.{tool}` span wrapping the call lifecycle to complete the propagation chain. This is the natural continuation of 32-01's extraction work.
- **_execute_call_inner extraction**: Split execution logic into inner method so the span `with` block cleanly wraps the full call lifecycle without deeply nesting the existing ~200 line method.
- **Per-test TracerProvider isolation**: OTEL only allows `set_tracer_provider()` once per process. Tests use `provider.get_tracer()` directly and patch the executor's `get_tracer` import, avoiding interference with other test modules.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added batch.call.{tool} span creation in _execute_call**
- **Found during:** Task 1 (writing integration test)
- **Issue:** Plan 32-01 wired `extract_trace_context()` and stored `parent_context`, but no span was created using it. The E2E test requires a span with the agent's trace_id and parent_span_id.
- **Fix:** Added `tracer.start_as_current_span(f"batch.call.{call.tool}", context=parent_context)` wrapping the call execution. Extracted inner method to keep span lifecycle clean.
- **Files modified:** src/mcp_hangar/server/tools/batch/executor.py
- **Verification:** Both integration tests pass; 2631 existing tests pass (no regressions)
- **Committed in:** b70d292 (Task 1 commit)

**2. [Rule 1 - Bug] Fixed OTEL test isolation for set_tracer_provider once-only constraint**
- **Found during:** Task 1 (test running in full suite)
- **Issue:** Plan's test fixture used `otel_trace.set_tracer_provider()` which fails silently when other test modules have already set a provider (OTEL only allows this once per process)
- **Fix:** Tests create isolated TracerProvider and patch `get_tracer` in executor module instead of relying on the global provider
- **Files modified:** tests/integration/test_trace_propagation_e2e.py
- **Verification:** Full Phase 32 suite (8 tests) passes when run together with unit tests
- **Committed in:** b70d292 (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (1 blocking, 1 bug)
**Impact on plan:** Both essential for test correctness. Span creation completes the propagation chain. Test isolation ensures reliable CI.

## Issues Encountered

Pre-existing test failures remain unchanged (not caused by this plan):
- `tests/unit/test_event_serialization_fuzz.py` - TypeError in ProviderQuarantine (collection error)
- `tests/feature/test_descriptions.py` - TypeError in format string
- `tests/unit/test_cli_status.py::test_get_status_missing_config` - pre-existing failure

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 32 complete: all 3 plans delivered (extract + inject + e2e test)
- W3C TraceContext propagation verified end-to-end with InMemorySpanExporter
- Ready for Phase 33 (OTLP Completeness for Security Events)
- v0.13.0 gate: Phases 31-32 both complete

---
*Phase: 32-trace-context-propagation*
*Completed: 2026-03-24*
