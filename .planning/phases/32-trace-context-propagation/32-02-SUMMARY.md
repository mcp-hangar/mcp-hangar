---
phase: 32-trace-context-propagation
plan: 02
subsystem: observability
tags: [otel, tracing, w3c-tracecontext, http-client, distributed-tracing, tdd]

# Dependency graph
requires:
  - phase: 32-01
    provides: "extract_trace_context wired into BatchExecutor (inbound); CallSpec.metadata field"
  - phase: 31-otel-semantic-conventions
    provides: "inject_trace_context() and extract_trace_context() in tracing.py"
provides:
  - "W3C TraceContext injection into outbound HTTP provider requests via HttpClient.call()"
  - "traceparent header propagated from Hangar to remote HTTP MCP providers"
  - "Verified StdioClient correctly excluded from trace injection (no header mechanism)"
affects: [32-03, 33-otlp-completeness]

# Tech tracking
tech-stack:
  added: []
  patterns: ["W3C TraceContext injection at HttpClient.call() outbound request path"]

key-files:
  created:
    - tests/unit/test_trace_context_injection.py
  modified:
    - src/mcp_hangar/http_client.py

key-decisions:
  - "Inject in HttpClient.call() rather than HttpLauncher.launch() -- the actual HTTP request happens in HttpClient"
  - "Pass trace_headers via httpx.post(headers=...) which merges with base client headers"
  - "Empty trace_headers dict passed as None to avoid unnecessary header merge when no trace active"

patterns-established:
  - "Trace context injection at HTTP client call: trace_headers = {}; inject_trace_context(trace_headers); client.post(..., headers=trace_headers)"

requirements-completed: []

# Metrics
duration: 4min
completed: 2026-03-24
---

# Phase 32 Plan 02: Outbound Trace Context Injection in HttpClient Summary

**Wired inject_trace_context() into HttpClient.call() to propagate W3C TraceContext (traceparent) headers to remote HTTP MCP providers, enabling end-to-end distributed tracing from agent through Hangar to provider**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-24T14:13:16Z
- **Completed:** 2026-03-24T14:17:28Z
- **Tasks:** 3
- **Files modified:** 2

## Accomplishments
- Wired inject_trace_context() into HttpClient.call() outbound request path
- traceparent header now propagated in every outbound HTTP provider request when an active trace exists
- Verified StdioClient correctly excluded from trace injection (no header mechanism for JSON-RPC over stdio)
- 3 TDD tests covering: inject called on outbound request, traceparent present with active trace, stdio exclusion

## Task Commits

Each task was committed atomically:

1. **Task 1: Read HttpLauncher to find outbound request site** - No commit (read-only analysis task)
2. **Task 2: RED -- Write failing tests for outbound trace context injection** - `edb5075` (test)
3. **Task 3: GREEN -- Wire inject_trace_context into HttpClient.call()** - `38f0a3a` (feat)

_TDD plan: RED -> GREEN cycle. No REFACTOR needed -- implementation is minimal and clean._

## Files Created/Modified
- `tests/unit/test_trace_context_injection.py` - 3 tests: inject called, traceparent present, stdio excluded
- `src/mcp_hangar/http_client.py` - Import inject_trace_context, call before httpx.post() in call()

## Decisions Made
- **Injection in HttpClient, not HttpLauncher**: The plan targeted `http.py` (HttpLauncher) but the actual outbound HTTP request happens in `http_client.py` (HttpClient.call()). The plan acknowledged this possibility: "The implementation goes in the HTTP client that HttpLauncher.launch() returns (or the method that actually makes requests)." HttpClient.call() is the correct injection point.
- **httpx headers parameter**: Used `httpx.Client.post(headers=...)` which merges extra headers with the client's base headers (auth, content-type). This means trace headers coexist cleanly with auth and custom headers.
- **Conditional None**: Pass `headers=None` when trace_headers is empty to avoid unnecessary empty dict merge.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Injection target changed from http.py to http_client.py**
- **Found during:** Task 1 (read-only analysis)
- **Issue:** Plan listed `src/mcp_hangar/domain/services/provider_launcher/http.py` as the target file, but HttpLauncher.launch() only creates and returns an HttpClient -- the actual HTTP calls happen in HttpClient.call() in `src/mcp_hangar/http_client.py`
- **Fix:** Implemented injection in HttpClient.call() where the outbound HTTP request is actually made
- **Files modified:** src/mcp_hangar/http_client.py (instead of http.py)
- **Verification:** All 3 tests pass; 2642 existing tests pass (no regressions)
- **Committed in:** 38f0a3a (Task 3 commit)

**2. [Rule 1 - Bug] Fixed InMemorySpanExporter import path in test**
- **Found during:** Task 2 (RED phase test run)
- **Issue:** Test used `opentelemetry.sdk.trace.export.in_memory` but correct path is `opentelemetry.sdk.trace.export.in_memory_span_exporter`
- **Fix:** Updated import path in test
- **Files modified:** tests/unit/test_trace_context_injection.py
- **Verification:** Test imports correctly and fails for the right reason (no injection yet)
- **Committed in:** edb5075 (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (1 blocking, 1 bug)
**Impact on plan:** Both essential for correctness. The injection target change follows the plan's own guidance. No scope creep.

## Issues Encountered

Pre-existing test failures remain unchanged (not caused by this plan):
- `tests/unit/test_event_serialization_fuzz.py` - TypeError in ProviderQuarantine (collection error)
- `tests/feature/test_descriptions.py` - TypeError in format string
- `tests/unit/test_cli_status.py::test_get_status_missing_config` - pre-existing failure

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Outbound trace context injection is wired into HttpClient (HTTP providers)
- Inbound trace context extraction is wired into BatchExecutor (from 32-01)
- Ready for 32-03: end-to-end test with InMemorySpanExporter validating trace_id correlation across agent -> BatchExecutor -> HttpClient
- StdioClient correctly excluded from trace injection (design decision verified by test)

---
*Phase: 32-trace-context-propagation*
*Completed: 2026-03-24*
