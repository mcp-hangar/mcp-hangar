---
phase: 21-log-capture-infrastructure
plan: 02
subsystem: infrastructure
tags: [log-capture, stderr-reader, subprocess, docker, daemon-thread, python]

# Dependency graph
requires:
  - phase: 21-01
    provides: LogLine dataclass and IProviderLogBuffer contract from domain layer
provides:
  - Provider._start_stderr_reader() daemon thread reading process.stderr line-by-line
  - Provider.set_log_buffer() injection method under Provider._lock
  - DockerLauncher stderr=subprocess.PIPE (was DEVNULL)
  - SubprocessLauncher stderr=subprocess.PIPE (was DEVNULL)
  - Daemon threads named stderr-reader-{provider_id} that terminate cleanly on EOF
affects: [21-03, 22-01, 22-02, 22-03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Daemon thread pattern for live stderr capture -- thread auto-exits when process exits
    - BLE001 fault-barrier in reader loop -- exceptions swallowed, thread never crashes on pipe error
    - set_log_buffer injection under Provider._lock -- safe buffer assignment after construction
    - _create_client calls _start_stderr_reader only when _log_buffer is not None

key-files:
  created:
    - packages/core/tests/unit/test_stderr_reader.py
  modified:
    - packages/core/mcp_hangar/domain/model/provider.py
    - packages/core/mcp_hangar/domain/services/provider_launcher/docker.py
    - packages/core/mcp_hangar/domain/services/provider_launcher/subprocess.py

key-decisions:
  - "Reader thread uses BLE001 noqa fault-barrier -- pipe errors silently swallowed to prevent thread crash"
  - "_start_stderr_reader is a no-op when process or stderr is None -- safe for HTTP transport"
  - "_create_client guards _start_stderr_reader with _log_buffer is not None check"

patterns-established:
  - "Pattern 1: Daemon stderr reader thread -- spawned per-provider, exits on EOF, fault-barrier on errors"
  - "Pattern 2: Buffer injection guard -- stderr reader only started when buffer has been injected"

requirements-completed: [LOG-02]

# Metrics
duration: 1min
completed: 2026-03-15
---

# Phase 21 Plan 02: Live Stderr Reader Threads Summary

**Provider._start_stderr_reader daemon threads capture process.stderr line-by-line into log buffers; DockerLauncher and SubprocessLauncher changed from DEVNULL to PIPE to enable live streaming**

## Performance

- **Duration:** 1 min
- **Started:** 2026-03-15T10:01:16Z
- **Completed:** 2026-03-15T10:02:00Z
- **Tasks:** 2 (verification tasks -- code was pre-implemented)
- **Files modified:** 4

## Accomplishments

- Verified Provider._start_stderr_reader spawns named daemon thread (stderr-reader-{provider_id}) when stderr pipe is available
- Verified reader thread appends LogLine(stream="stderr") with newline-stripped content for each line read
- Verified fault-barrier pattern: exceptions in reader loop swallowed with BLE001 noqa, thread exits cleanly
- Verified _create_client only calls _start_stderr_reader when_log_buffer is injected (not None)
- Verified DockerLauncher uses stderr=subprocess.PIPE (previously DEVNULL -- critical change for live streaming)
- Verified SubprocessLauncher uses stderr=subprocess.PIPE
- All 25 test_stderr_reader.py tests pass

## Task Commits

Each task was committed atomically (pre-implemented, committed before plan execution):

1. **Task 1: Verify Provider._start_stderr_reader and set_log_buffer** - `b0cdba3` (feat -- pre-implemented)
2. **Task 2: Verify DockerLauncher and SubprocessLauncher stderr=subprocess.PIPE** - `b0cdba3` (feat -- pre-implemented)

**Plan metadata:** _(this summary commit -- docs: complete plan)_

_Note: Both tasks verified pre-implemented code. The implementation commit b0cdba3 contains all files._

## Files Created/Modified

- `packages/core/mcp_hangar/domain/model/provider.py` - Added Provider.set_log_buffer() and Provider._start_stderr_reader()
- `packages/core/mcp_hangar/domain/services/provider_launcher/docker.py` - Changed stderr=DEVNULL to stderr=PIPE
- `packages/core/mcp_hangar/domain/services/provider_launcher/subprocess.py` - Confirmed stderr=PIPE
- `packages/core/tests/unit/test_stderr_reader.py` - 25 unit tests covering thread lifecycle, LogLine appending, fault-barrier behavior

## Decisions Made

- BLE001 fault-barrier in reader loop -- pipe errors (e.g., process killed mid-read) must not crash the reader thread
- _start_stderr_reader is a no-op for None process or None stderr -- safe for HTTP remote transport
- Buffer injection guard ensures no orphaned threads when bootstrap hasn't injected buffer yet

## Deviations from Plan

None - plan executed exactly as written. All pre-implemented files matched specifications exactly. All 25 unit tests passed on first run.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Self-Check

- [x] `packages/core/mcp_hangar/domain/model/provider.py` contains `_start_stderr_reader` -- FOUND
- [x] `packages/core/mcp_hangar/domain/services/provider_launcher/docker.py` contains `stderr=subprocess.PIPE` -- FOUND
- [x] `packages/core/mcp_hangar/domain/services/provider_launcher/subprocess.py` contains `stderr=subprocess.PIPE` -- FOUND
- [x] `packages/core/tests/unit/test_stderr_reader.py` -- FOUND
- [x] Commit `b0cdba3` exists in git log
- [x] All 25 test_stderr_reader.py tests passed

## Self-Check: PASSED

## Next Phase Readiness

- Live stderr capture layer complete: launchers expose PIPE, Provider reads line-by-line into buffer
- Ready for Plan 21-03 (GET /api/providers/{id}/logs REST endpoint)
- Ready for Phase 22 (LogStreamBroadcaster + WebSocket endpoint + UI)

---
_Phase: 21-log-capture-infrastructure_
_Completed: 2026-03-15_
