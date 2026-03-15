# Phase 21: Log Capture Infrastructure - Context

**Gathered:** 2026-03-14
**Status:** Ready for planning

<domain>
## Phase Boundary

Per-provider log ring buffers exist, launchers stream stderr lines into them in real time, and a REST endpoint exposes log history. This is the capture layer — WebSocket streaming is Phase 22.

Scope is fixed: LOG-01 (domain types + ring buffer + registry), LOG-02 (stderr-reader threads in launchers), LOG-03 (REST endpoint for log history).

</domain>

<decisions>
## Implementation Decisions

## Pre-implemented code

Almost all Phase 21 and Phase 22 implementation is already in the codebase (written ahead of formal planning). The planning cycle should treat this as retroactive documentation — plans and summaries are written to record what was built, not to instruct new work. The one confirmed gap is the integration test required by Phase 22 criterion 2 (`tests/integration/test_log_streaming.py`).

Existing implementation status:

- `LogLine` value object: `domain/value_objects/log.py` — complete
- `IProviderLogBuffer` interface: `domain/contracts/log_buffer.py` — complete
- `ProviderLogBuffer` ring buffer + singleton registry: `infrastructure/persistence/log_buffer.py` — complete
- Stderr-reader daemon thread: `domain/model/provider.py:_start_stderr_reader()` — complete (wired after `_create_client()`, not inside launchers)
- `DockerLauncher`: already uses `stderr=subprocess.PIPE` — complete
- `GET /api/providers/{id}/logs`: `server/api/providers.py:119` — complete
- Unit tests (70 passing): `tests/unit/test_log_*.py` — complete
- Phase 22 WebSocket + broadcaster + UI: also pre-implemented

## Dynamic provider buffers

Buffers for runtime-discovered or manually-registered providers must follow this lifecycle:

- **Create on registration**: when a `ProviderRegistered` domain event fires, create and wire a buffer immediately. The buffer is available before the provider first starts.
- **Remove on deregistration**: when a `ProviderDeregistered` event fires, remove the buffer from the singleton registry and release memory.
- **Mechanism**: event-driven via existing `EventBus` subscriptions — consistent with the existing event-handler architecture. No polling, no direct coupling in command handlers.
- **Capacity**: 1000 lines (existing `DEFAULT_MAX_LINES`) for all providers regardless of mode or source. No per-provider configuration needed.

## stdout capture

Not in scope for Phase 21 or Phase 22. The `LogLine` value object supports `stream: Literal["stdout", "stderr"]` for future use, but all reader threads capture only stderr. The REST endpoint and WebSocket naturally pass through whatever stream value is stored.

## Claude's Discretion

- Exact event handler class name and file location for the dynamic buffer wiring
- Whether to add `ProviderRegistered` / `ProviderDeregistered` event subscriptions to an existing handler or a new `LogBufferEventHandler`
- Test structure for the Phase 22 integration test (fixture design, assertion style — follow existing `test_saga_compensation.py` patterns)

</decisions>

<specifics>
## Specific Ideas

- The stderr-reader thread implementation in `Provider._start_stderr_reader()` is architecturally cleaner than putting it in the launchers (launchers just launch, Provider orchestrates its own lifecycle). Keep it in the Provider aggregate.
- The `on_append` callback pattern in `ProviderLogBuffer` cleanly decouples the ring buffer from the WebSocket broadcaster — preserve this design.

</specifics>

<code_context>

## Existing Code Insights

### Reusable Assets

- `ProviderLogBuffer` + `set_log_buffer` / `get_log_buffer` registry: `infrastructure/persistence/log_buffer.py` — production-ready, thread-safe
- `LogStreamBroadcaster.notify()` as `on_append` callback: `server/api/ws/logs.py` — already used in `server/bootstrap/logs.py`
- `init_log_buffers(providers)`: `server/bootstrap/logs.py` — handles bootstrap case; needs a symmetric event-handler for runtime case
- `EventBus` subscription pattern: `infrastructure/event_bus.py` — existing handlers in `application/event_handlers/` show how to subscribe

### Established Patterns

- Event handlers live in `application/event_handlers/` and are registered in `server/bootstrap/event_handlers.py`
- Background workers follow the daemon-thread pattern with try/except fault-barrier (BLE001 noqa required)
- All shared state access uses `threading.Lock` — `ProviderLogBuffer` and `LogStreamBroadcaster` already follow this

### Integration Points

- `server/bootstrap/__init__.py:264` calls `init_log_buffers(PROVIDERS)` — this is the bootstrap path
- `server/api/providers.py:190` routes `GET /{provider_id}/logs` — REST endpoint is live
- `server/api/ws/__init__.py:15` routes `WebSocketRoute("/providers/{provider_id}/logs", ...)` — WS endpoint is live
- Domain events for provider registration: `domain/events.py` — check `ProviderRegistered` / `ProviderDeregistered` event names
- `application/event_handlers/` — location for new `LogBufferEventHandler`

</code_context>

<deferred>
## Deferred Ideas

- stdout capture (subprocess `process.stdout` reader thread) — `LogLine.stream` already supports "stdout" for when this is added
- Per-provider `log_buffer_size` YAML config — explicitly not needed now; revisit if operators hit memory pressure

</deferred>

---

*Phase: 21-log-capture-infrastructure*
*Context gathered: 2026-03-14*
