---
phase: 21-log-capture-infrastructure
verified: 2026-03-15T12:00:00Z
status: passed
score: 10/10 must-haves verified
re_verification: false
---

# Phase 21: Log Capture Infrastructure Verification Report

**Phase Goal:** Per-provider log ring buffers exist, launchers stream stderr lines into them in real time, and a REST endpoint exposes log history — establishing the capture layer before WebSocket streaming is added.
**Verified:** 2026-03-15T12:00:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | LogLine is a frozen dataclass with all four fields and to_dict() | ✓ VERIFIED | `domain/value_objects/log.py` — `@dataclass(frozen=True)` with `provider_id`, `stream: Literal["stdout","stderr"]`, `content`, `recorded_at: float = field(default_factory=time.time)`; `to_dict()` returns all four keys |
| 2 | IProviderLogBuffer is an ABC with append, tail, clear, provider_id | ✓ VERIFIED | `domain/contracts/log_buffer.py` — ABC with all four abstract members; thread-safety documented |
| 3 | ProviderLogBuffer wraps deque(maxlen=N) with threading.Lock | ✓ VERIFIED | `infrastructure/persistence/log_buffer.py` — `deque[LogLine]` with `threading.Lock`; on_append called outside lock |
| 4 | Singleton registry: get/set/get_or_create/remove/clear all present | ✓ VERIFIED | All five registry functions present and correct; protected by `_registry_lock` |
| 5 | init_log_buffers creates ProviderLogBuffer per provider with broadcaster.notify | ✓ VERIFIED | `server/bootstrap/logs.py` — lazy import of `get_log_broadcaster()`, creates `ProviderLogBuffer(on_append=broadcaster.notify)`, calls `set_log_buffer()` and `provider.set_log_buffer()` |
| 6 | Provider._start_stderr_reader spawns named daemon thread reading stderr line-by-line | ✓ VERIFIED | `domain/model/provider.py` lines 614-653 — `threading.Thread(daemon=True, name=f"stderr-reader-{provider_id}")`, iterates `stderr_pipe`, strips `\n`, appends `LogLine(stream="stderr")` |
| 7 | Reader thread terminates cleanly on EOF; exceptions swallowed (BLE001) | ✓ VERIFIED | `except Exception:  # noqa: BLE001` at line 649; loop exits naturally on pipe EOF |
| 8 | DockerLauncher and SubprocessLauncher use stderr=subprocess.PIPE | ✓ VERIFIED | `docker.py` line 221: `stderr=subprocess.PIPE  # Capture stderr for live log streaming`; `subprocess.py` line 260: `stderr=subprocess.PIPE  # Capture stderr for error diagnostics` |
| 9 | GET /api/providers/{provider_id}/logs returns correct JSON shape with lines clamping | ✓ VERIFIED | `server/api/providers.py` lines 119-148 — default 100, `min(max(1, lines), 1000)`, invalid falls back to 100, dispatches `GetProviderQuery` for 404, returns `{logs, provider_id, count}` |
| 10 | All phase 21 unit tests pass | ✓ VERIFIED | `test_log_buffer.py` + `test_bootstrap_logs.py` + `test_stderr_reader.py` + `test_api_provider_logs.py` — 64 tests pass in 2.54s |

**Score:** 10/10 truths verified

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `packages/core/mcp_hangar/domain/value_objects/log.py` | LogLine frozen dataclass | ✓ VERIFIED | 39 lines; frozen dataclass with `Literal`, `field(default_factory=time.time)`, `to_dict()` |
| `packages/core/mcp_hangar/domain/contracts/log_buffer.py` | IProviderLogBuffer ABC | ✓ VERIFIED | 52 lines; ABC with 4 abstract members; imports LogLine from `..value_objects.log` |
| `packages/core/mcp_hangar/infrastructure/persistence/log_buffer.py` | ProviderLogBuffer + registry | ✓ VERIFIED | 172 lines; `DEFAULT_MAX_LINES=1000`, all 5 registry functions, `__len__`, on_append outside lock |
| `packages/core/mcp_hangar/server/bootstrap/logs.py` | init_log_buffers() wiring | ✓ VERIFIED | 51 lines; lazy imports, snapshots keys with `list(providers.keys())`, injects broadcaster |
| `packages/core/mcp_hangar/domain/model/provider.py` | Provider._start_stderr_reader + set_log_buffer | ✓ VERIFIED | `set_log_buffer` at line 397 (under `Provider._lock`); `_start_stderr_reader` at line 614; `_create_client` guards at lines 609-610 |
| `packages/core/mcp_hangar/domain/services/provider_launcher/docker.py` | DockerLauncher stderr=PIPE | ✓ VERIFIED | `stderr=subprocess.PIPE` at line 221 (previously DEVNULL — correctly changed) |
| `packages/core/mcp_hangar/domain/services/provider_launcher/subprocess.py` | SubprocessLauncher stderr=PIPE | ✓ VERIFIED | `stderr=subprocess.PIPE` at line 260 |
| `packages/core/mcp_hangar/server/api/providers.py` | get_provider_logs handler + route | ✓ VERIFIED | Handler at line 119; route `/{provider_id:str}/logs` registered at line 190 |
| `packages/core/tests/unit/test_log_buffer.py` | Unit tests: LogLine, buffer, registry | ✓ VERIFIED | Present; tests pass |
| `packages/core/tests/unit/test_bootstrap_logs.py` | Unit tests: init_log_buffers | ✓ VERIFIED | Present; tests pass |
| `packages/core/tests/unit/test_stderr_reader.py` | Unit tests: stderr reader lifecycle | ✓ VERIFIED | Present; tests pass |
| `packages/core/tests/unit/test_api_provider_logs.py` | Unit tests: REST endpoint | ✓ VERIFIED | Present; tests pass |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `infrastructure/persistence/log_buffer.py` | `domain/contracts/log_buffer.py` | `class ProviderLogBuffer(IProviderLogBuffer)` | ✓ WIRED | `from ...domain.contracts.log_buffer import IProviderLogBuffer`; class declaration line 26 |
| `server/bootstrap/logs.py` | `infrastructure/persistence/log_buffer.py` | `ProviderLogBuffer(on_append=broadcaster.notify)` | ✓ WIRED | Lazy import at line 33; `on_append=broadcaster.notify` at line 46 |
| `server/bootstrap/logs.py` | `server/api/ws/logs.py` | `get_log_broadcaster()` | ✓ WIRED | Lazy import at line 34; called at line 36; `LogStreamBroadcaster` singleton exists in `ws/logs.py` line 42 |
| `domain/model/provider.py` | `domain/value_objects/log.py` | `LogLine` import inside `_start_stderr_reader` | ✓ WIRED | Top-level import at line 12 (`IProviderLogBuffer`); lazy `LogLine` import inside `_start_stderr_reader` at line 632 |
| `domain/model/provider.py` | `domain/contracts/log_buffer.py` | `IProviderLogBuffer` type annotation | ✓ WIRED | `from ..contracts.log_buffer import IProviderLogBuffer` at line 12 |
| `server/api/providers.py` | `infrastructure/persistence/log_buffer.py` | `get_log_buffer(provider_id)` | ✓ WIRED | Import at line 20; called at line 142 in `get_provider_logs` |
| `server/api/providers.py` | `application/queries` | `dispatch_query(GetProviderQuery(...))` | ✓ WIRED | Import at line 15; called at line 140 before buffer lookup |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| LOG-01 | 21-01-PLAN.md | LogLine, IProviderLogBuffer, ProviderLogBuffer ring buffer, singleton registry | ✓ SATISFIED | All domain types implemented; registry functions present; imports verified; 39 tests pass |
| LOG-02 | 21-02-PLAN.md | Daemon stderr-reader threads; DockerLauncher DEVNULL→PIPE; SubprocessLauncher PIPE | ✓ SATISFIED | `_start_stderr_reader` with BLE001 fault-barrier; both launchers use PIPE; 25 tests pass |
| LOG-03 | 21-03-PLAN.md | GET /api/providers/{id}/logs with lines clamping, 404, empty-list semantics | ✓ SATISFIED | Endpoint implemented with exact spec; 17 tests pass |

No orphaned requirements — REQUIREMENTS.md maps LOG-01/02/03 exclusively to Phase 21. LOG-04/05 belong to Phase 22.

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| — | — | None found | — | — |

All phase 21 files are clean:

- No TODO/FIXME/HACK/PLACEHOLDER comments
- No empty implementations (`return null`, `return {}`, `return []`)
- No stub handlers
- on_append callback correctly called outside the threading.Lock (no I/O under lock)
- BLE001 fault-barrier on reader thread is intentional per CLAUDE.md policy

---

## Human Verification Required

None. All phase goals are verifiable programmatically:

- Ring buffer semantics confirmed by direct Python invocation
- Daemon thread lifecycle covered by unit tests
- REST endpoint response shape verified by import check + test suite
- No visual/UI components in this phase (those are Phase 22/LOG-05)

---

## Commits Verified

| Commit | Plan | Description |
|--------|------|-------------|
| `d11ba6f` | 21-01 | feat: LogLine, IProviderLogBuffer, ProviderLogBuffer, singleton registry (LOG-01) |
| `b0cdba3` | 21-02 | feat: live stderr-reader threads, DockerLauncher DEVNULL→PIPE, Provider log_buffer injection (LOG-02) |
| `7b45366` | 21-03 | feat: GET /api/providers/{id}/logs REST endpoint (LOG-03) |

All three commits confirmed present in `git log`.

---

## Summary

Phase 21 goal fully achieved. The log capture infrastructure layer is complete and correctly wired:

1. **Ring buffers (LOG-01)**: `LogLine` frozen dataclass, `IProviderLogBuffer` ABC, `ProviderLogBuffer` deque ring buffer, and a thread-safe singleton registry are all present, substantive, and correctly wired. Re-exports in both `domain/value_objects/__init__.py` and `domain/contracts/__init__.py` confirmed.

2. **Live stderr streaming (LOG-02)**: `Provider._start_stderr_reader` spawns a named daemon thread (`stderr-reader-{provider_id}`) that reads stderr line-by-line, strips newlines, and appends `LogLine(stream="stderr")` entries. The BLE001 fault-barrier ensures the thread cannot crash. Both `DockerLauncher` and `SubprocessLauncher` use `stderr=subprocess.PIPE`. The `_create_client` guard ensures no orphaned threads when no buffer is injected.

3. **REST endpoint (LOG-03)**: `GET /api/providers/{provider_id}/logs` is registered, returns `{logs, provider_id, count}`, clamps `lines` to `[1, 1000]`, dispatches `GetProviderQuery` for proper 404 semantics, and returns an empty list for providers with no buffer.

64 unit tests pass. No anti-patterns. Phase is ready for Phase 22 (WebSocket streaming + UI).

---

_Verified: 2026-03-15T12:00:00Z_
_Verifier: Claude (gsd-verifier)_
