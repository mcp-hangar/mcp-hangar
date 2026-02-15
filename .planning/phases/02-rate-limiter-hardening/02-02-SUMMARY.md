---
phase: 02-rate-limiter-hardening
plan: 02
subsystem: authentication
tags: [security, rate-limiting, domain-events, cleanup, audit-trail]
dependency_graph:
  requires:
    - 02-01 (AuthRateLimiter exponential backoff)
  provides:
    - RateLimitLockout/RateLimitUnlock domain events for audit trail
    - Hardened cleanup worker with edge case handling
    - force_cleanup() method for testing and manual intervention
  affects:
    - Event-driven systems consuming rate limiter events
    - Monitoring and alerting systems (can subscribe to lockout events)
tech_stack:
  added: []
  patterns:
    - Domain event emission for security state changes
    - Safe event publishing with exception isolation
    - Cleanup worker refactoring for testability
key_files:
  created: []
  modified:
    - packages/core/mcp_hangar/domain/events.py
    - packages/core/mcp_hangar/infrastructure/auth/rate_limiter.py
    - packages/core/tests/unit/test_auth_rate_limiter.py
decisions:
  - "event_publisher is optional (None by default) for backward compatibility"
  - "Event publishing never raises (caught in _publish_event helper)"
  - "Cleanup emits unlock events for expired lockouts found during cleanup (unlock_reason: cleanup)"
  - "Refactored _maybe_cleanup into _do_cleanup for reuse by force_cleanup()"
  - "force_cleanup() returns count of removed trackers for verification"
metrics:
  duration_minutes: 4
  tasks_completed: 2
  tests_added: 10
  files_modified: 3
  commits: 2
  completed_date: 2026-02-15
---

# Phase 2 Plan 2: Rate Limiter Domain Events and Cleanup Hardening Summary

**One-liner:** AuthRateLimiter now emits RateLimitLockout/Unlock domain events for audit trail and has hardened cleanup with force_cleanup() method.

## What Was Built

Added domain event emission to AuthRateLimiter for observability and audit trail compliance. Hardened the cleanup worker to handle edge cases (expired lockouts never re-checked, concurrent cleanup calls) and added manual cleanup support.

**Key Components:**

1. **Domain Events (Task 1):**
   - Added RateLimitLockout event (source_ip, lockout_duration_seconds, lockout_count, failed_attempts)
   - Added RateLimitUnlock event (source_ip, lockout_count, unlock_reason)
   - unlock_reason values: "expired", "success", "manual_clear", "cleanup"

2. **Event Emission (Task 1):**
   - AuthRateLimiter accepts optional event_publisher callback (None by default)
   - _publish_event helper with exception safety (logs warning, never raises)
   - RateLimitLockout emitted on lockout trigger (check_rate_limit)
   - RateLimitUnlock emitted on:
     - Lockout expiry detected during check_rate_limit (unlock_reason: expired)
     - Successful authentication with locked IP (unlock_reason: success)
     - Manual clear of locked IP (unlock_reason: manual_clear)

3. **Hardened Cleanup (Task 2):**
   - Refactored _maybe_cleanup to delegate to _do_cleanup for reuse
   - Added force_cleanup() public method returning count of removed trackers
   - Cleanup emits RateLimitUnlock for expired lockouts found during cleanup (unlock_reason: cleanup)
   - Correctly handles expired lockouts that were never re-checked
   - Thread-safe (runs under self._lock)

## Test Coverage

**New Tests (10 total):**

- TestAuthRateLimiterDomainEvents (5 tests):
  - Lockout emits RateLimitLockout event
  - Lockout expiry emits unlock event
  - Success emits unlock event if locked
  - Success does not emit unlock if not locked
  - No event_publisher does not raise

- TestAuthRateLimiterCleanup (5 tests):
  - Cleanup runs after interval
  - Cleanup preserves active lockouts
  - Cleanup removes expired lockouts with no activity
  - Cleanup emits unlock events for expired lockouts
  - force_cleanup returns removed count

**Total AuthRateLimiter tests: 29 (all passing)**

**Regression Tests:**

- test_auth_middleware.py: 30 passed (no regressions)
- test_auth_security.py: 20 passed (no regressions)

## Deviations from Plan

**None** - Plan executed exactly as written. All event emission paths implemented, cleanup hardening complete, tests comprehensive.

## Technical Notes

**Event Publishing Safety:**

```python
def _publish_event(self, event: object) -> None:
    if self._event_publisher:
        try:
            self._event_publisher(event)
        except Exception as e:
            logger.warning("rate_limiter_event_publish_failed", event_type=type(event).__name__, error=str(e))
```

Event publishing failures never disrupt rate limiting operations. Logged as warnings for monitoring.

**Cleanup Refactoring:**

```python
def _maybe_cleanup(self, now: float) -> None:
    if now - self._last_cleanup < self._config.cleanup_interval:
        return
    self._do_cleanup(now)

def _do_cleanup(self, now: float) -> int:
    # Core cleanup logic
    return len(to_remove)

def force_cleanup(self) -> int:
    now = time.time()
    with self._lock:
        return self._do_cleanup(now)
```

_do_cleanup is the workhorse. _maybe_cleanup checks interval. force_cleanup allows manual triggering.

**Cleanup Edge Cases Addressed:**

1. **Expired lockouts never re-checked:** Cleanup now checks for `locked_until is not None and now >= locked_until` and emits unlock event before removal.
2. **Concurrent cleanup:** Already safe (runs under RLock, re-entrant).
3. **Timer drift:** Not a concern (cleanup interval is 300s, drift negligible).
4. **Trackers added during iteration:** Safe (builds to_remove list, then deletes).

## Event Examples

**RateLimitLockout:**

```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "occurred_at": 1771171200.5,
  "source_ip": "192.168.1.100",
  "lockout_duration_seconds": 300.0,
  "lockout_count": 1,
  "failed_attempts": 10
}
```

**RateLimitUnlock:**

```json
{
  "event_id": "660e8400-e29b-41d4-a716-446655440001",
  "occurred_at": 1771171500.7,
  "source_ip": "192.168.1.100",
  "lockout_count": 1,
  "unlock_reason": "expired"
}
```

## Integration

**Wiring event_publisher:**

```python
from mcp_hangar.infrastructure.auth.rate_limiter import AuthRateLimiter, AuthRateLimitConfig
from mcp_hangar.infrastructure.event_bus import EventBus

event_bus = EventBus()
config = AuthRateLimitConfig(max_attempts=5, lockout_seconds=300)
limiter = AuthRateLimiter(config, event_publisher=event_bus.publish)
```

**Subscribing to events:**

```python
def on_lockout(event: RateLimitLockout):
    if event.lockout_count >= 3:
        alert_security_team(event.source_ip)

event_bus.subscribe(RateLimitLockout, on_lockout)
```

**Manual cleanup:**

```python
# Force cleanup (e.g., during testing or on-demand)
removed_count = limiter.force_cleanup()
logger.info("manual_cleanup_complete", removed_count=removed_count)
```

## Self-Check: PASSED

**Domain events exist:**

```bash
grep -q "class RateLimitLockout" packages/core/mcp_hangar/domain/events.py && echo "FOUND"
# FOUND
grep -q "class RateLimitUnlock" packages/core/mcp_hangar/domain/events.py && echo "FOUND"
# FOUND
```

**Event emission wired:**

```bash
grep -q "event_publisher" packages/core/mcp_hangar/infrastructure/auth/rate_limiter.py && echo "FOUND"
# FOUND
```

**Cleanup method exists:**

```bash
grep -q "force_cleanup" packages/core/mcp_hangar/infrastructure/auth/rate_limiter.py && echo "FOUND"
# FOUND
```

**Commits exist:**

```bash
git log --oneline --all | grep -q "5c3a7c0" && echo "FOUND: 5c3a7c0"
# FOUND: 5c3a7c0
git log --oneline --all | grep -q "2771ccf" && echo "FOUND: 2771ccf"
# FOUND: 2771ccf
```

**Tests pass:**

```bash
cd packages/core && python -m pytest tests/unit/test_auth_rate_limiter.py -v
# 29 passed, 1 warning
```

## Commits

- `5c3a7c0`: feat(02-02): add domain events for rate limiter lockout/unlock
- `2771ccf`: feat(02-02): harden cleanup worker for edge cases

## Duration

4 minutes (261 seconds)

## Next Steps

Phase 2 complete. Next phase: JWT token lifetime and key rotation.
