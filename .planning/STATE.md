# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-15)

**Core value:** Reliable, observable MCP provider management with production-grade lifecycle control
**Current focus:** Phase 4 - API Key Rotation

## Current Position

Phase: 4 of 4 (API Key Rotation)
Plan: 1 of 2 (InMemory store rotation complete)
Status: Plan 04-01 complete, ready for Plan 04-02 (SQLite/EventSourced rotation)
Last activity: 2026-02-15 - Plan 04-01 complete (10/10 tests pass, 0 regressions)

Progress: [████████░░] 87.5% (3.5 of 4 phases complete)

## Performance Metrics

**Velocity:**

- Total plans completed: 6
- Average duration: 4.1 minutes
- Total execution time: 0.35 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-timing-attack-prevention | 2 | 6.6m | 3.3m |
| 02-rate-limiter-hardening | 2 | 8.5m | 4.3m |
| 03-jwt-lifetime-enforcement | 1 | 3.9m | 3.9m |
| 04-api-key-rotation | 1 | 5.2m | 5.2m |

**Recent Trend:**

- Last 5 plans: 02-01 (4.5m), 02-02 (4.0m), 03-01 (3.9m), 04-01 (5.2m)
- Trend: Consistent velocity

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- 2026-02-15: Scope includes timing fix, rate limiter audit, JWT lifetime, key rotation
- 2026-02-15: Deferred API key IP binding (adds complexity without immediate threat)
- 2026-02-15: No research phase (well-understood security patterns)
- 2026-02-15: Use hmac.compare_digest for all hash comparisons (timing attack prevention)
- 2026-02-15: Iterate all dict entries without early exit (constant-time guarantee)
- 2026-02-15: Dummy hash comparison for SQL stores equalizes code path timing
- 2026-02-15: Optional index_entry parameter maintains backward compatibility in EventSourced store
- 2026-02-15: Exponential backoff uses factor^(count-1) to keep first lockout at base duration
- 2026-02-15: Lockout count persists through expiry, resets only on successful authentication
- 2026-02-15: Default escalation: 2.0x per lockout, capped at 3600s (1 hour)
- 2026-02-15: event_publisher is optional (None by default) for backward compatibility
- 2026-02-15: Event publishing never raises (caught in _publish_event helper)
- 2026-02-15: Cleanup emits unlock events for expired lockouts found during cleanup
- 2026-02-15: Refactored _maybe_cleanup into _do_cleanup for reuse by force_cleanup()
- 2026-02-15: Default max_token_lifetime to 3600 seconds (1 hour) balances usability and security
- 2026-02-15: Setting max_token_lifetime=0 disables the check (escape hatch)
- 2026-02-15: Enforce lifetime check before creating Principal (fail fast)
- 2026-02-15: Raise specific TokenLifetimeExceededError for clear debugging
- 2026-02-15: MCP_JWT_MAX_TOKEN_LIFETIME env var overrides YAML config
- 2026-02-15: Default grace period: 86400 seconds (24 hours) balances security and operational convenience
- 2026-02-15: Grace period tracked in _rotated_keys dict: old_key_hash -> (new_key_hash, grace_until)
- 2026-02-15: Old keys remain fully functional during grace period (no warnings, just works)
- 2026-02-15: After grace expires, raise ExpiredCredentialsError with clear rotation message
- 2026-02-15: Prevent rotating already-rotated keys to avoid cascading grace periods

### Pending Todos

None yet.

### Blockers/Concerns

None yet.

## Session Continuity

Last session: 2026-02-15
Stopped at: Completed 04-01-PLAN.md (API key rotation for InMemory store)
Resume file: None
