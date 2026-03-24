---
phase: 32-trace-context-propagation
plan: 01
subsystem: observability
tags: [otel, tracing, w3c-tracecontext, batch-executor, distributed-tracing]

# Dependency graph
requires:
  - phase: 31-otel-semantic-conventions
    provides: "extract_trace_context() and get_tracer() in tracing.py, conventions.py constants"
provides:
  - "W3C TraceContext extraction wired into BatchExecutor._execute_call"
  - "CallSpec.metadata field for carrying trace context headers"
  - "parent_context available for span creation in batch call path"
affects: [32-02, 32-03, 33-otlp-completeness]

# Tech tracking
tech-stack:
  added: []
  patterns: ["W3C TraceContext extraction at batch call entry point"]

key-files:
  created:
    - tests/unit/test_trace_context_extraction.py
  modified:
    - src/mcp_hangar/server/tools/batch/executor.py
    - src/mcp_hangar/server/tools/batch/models.py

key-decisions:
  - "Added metadata field to CallSpec (default None) rather than a separate carrier object"
  - "Extract trace context early in _execute_call before cancellation/timeout checks"
  - "Normalize None metadata to {} before passing to extract_trace_context"

patterns-established:
  - "Trace context extraction at batch call entry: metadata = call.metadata or {}; parent_context = extract_trace_context(metadata)"

requirements-completed: []

# Metrics
duration: 4min
completed: 2026-03-24
---

# Phase 32 Plan 01: Trace Context Extraction in BatchExecutor Summary

**Wired extract_trace_context() into BatchExecutor._execute_call to extract W3C TraceContext (traceparent) from incoming batch call metadata, enabling distributed trace correlation between agents and Hangar**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-24T14:06:21Z
- **Completed:** 2026-03-24T14:10:22Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments
- Wired the previously dead-code `extract_trace_context()` into the primary tool dispatch path (`BatchExecutor._execute_call`)
- Added `metadata` field to `CallSpec` dataclass for carrying W3C TraceContext headers (traceparent, tracestate)
- Created 3 unit tests covering: traceparent present, empty metadata, None metadata

## Task Commits

Each task was committed atomically:

1. **Task 1: Read BatchExecutor to locate span creation point** - No commit (read-only analysis task)
2. **Task 2: RED -- Write failing tests for trace context extraction** - `b657dfd` (test)
3. **Task 3: GREEN -- Wire extract_trace_context into BatchExecutor** - `478924f` (feat)

_TDD plan: RED -> GREEN cycle. No REFACTOR needed -- implementation is minimal and clean._

## Files Created/Modified
- `tests/unit/test_trace_context_extraction.py` - 3 tests verifying extract_trace_context is called with correct metadata
- `src/mcp_hangar/server/tools/batch/executor.py` - Import and call extract_trace_context at start of _execute_call
- `src/mcp_hangar/server/tools/batch/models.py` - Added metadata field to CallSpec dataclass

## Decisions Made
- **CallSpec.metadata field**: Added `metadata: dict[str, str] | None = None` to CallSpec rather than creating a separate carrier object. This keeps the data model simple and backward-compatible (all existing callers unaffected by default None).
- **Early extraction**: Extract trace context at the top of `_execute_call`, before cancellation checks. The extraction is lightweight (dict lookup) and the parent_context is needed for any spans that might wrap the call.
- **None normalization**: `call.metadata or {}` normalizes None to empty dict before passing to `extract_trace_context`, avoiding null pointer issues.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added metadata field to CallSpec**
- **Found during:** Task 2 (test creation)
- **Issue:** Plan assumed CallSpec had a `metadata` field, but it did not exist
- **Fix:** Added `metadata: dict[str, str] | None = None` to CallSpec dataclass
- **Files modified:** src/mcp_hangar/server/tools/batch/models.py
- **Verification:** All existing tests pass (field has default None, backward-compatible)
- **Committed in:** b657dfd (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Essential for plan completion. No scope creep -- the field is required for trace context to have a transport mechanism.

## Issues Encountered

Pre-existing test failures found (not caused by this plan's changes):
- `tests/unit/test_event_serialization_fuzz.py` - TypeError in ProviderQuarantine (collection error)
- `tests/feature/test_descriptions.py` - TypeError in format string
- `tests/unit/test_cli_status.py::test_get_status_missing_config` - pre-existing failure

All verified as pre-existing by running against the stashed (unchanged) codebase.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- extract_trace_context() is now wired into the batch executor call path
- parent_context is extracted but not yet passed to span creation (no spans created in executor yet)
- Ready for 32-02 (inject_trace_context into HttpLauncher outbound path)
- Ready for 32-03 (end-to-end test with InMemorySpanExporter)

---
*Phase: 32-trace-context-propagation*
*Completed: 2026-03-24*
