# Roadmap: MCP Hangar v0.9 Security Hardening

## Overview

This milestone hardens the authentication layer against known attack vectors and adds missing security controls before production deployment. Four independent security improvements: fix timing attack vulnerability in API key validation, audit and harden the existing rate limiter with exponential backoff and test coverage, add maximum JWT token lifetime enforcement, and implement API key rotation with grace period support.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3, 4): Planned milestone work
- Decimal phases (e.g., 2.1): Urgent insertions (marked with INSERTED)

- [x] **Phase 1: Timing Attack Prevention** - Eliminate timing leaks in API key validation
- [x] **Phase 2: Rate Limiter Hardening** - Audit, test, and enhance existing rate limiter
- [x] **Phase 3: JWT Lifetime Enforcement** - Add configurable maximum token lifetime
- [x] **Phase 4: API Key Rotation** - Support key rotation with grace period

## Phase Details

### Phase 1: Timing Attack Prevention

**Goal**: API key validation is resistant to timing attacks across all auth stores
**Depends on**: Nothing (first phase)
**Requirements**: TIME-01, TIME-02, TIME-03
**Success Criteria** (what must be TRUE):

  1. API key hash comparison uses hmac.compare_digest for constant-time validation
  2. All auth stores (InMemory, SQLite, Postgres, EventSourced) use constant-time lookup patterns
  3. Automated tests verify timing characteristics remain within acceptable bounds across all stores
  4. Security audit document updated with mitigation details
**Plans**: 2 plans

Plans:

- [x] 01-01-PLAN.md -- TDD: constant-time key lookup utility + InMemoryApiKeyStore hardening
- [x] 01-02-PLAN.md -- Apply constant-time to SQLite/Postgres/EventSourced + timing tests + audit doc

### Phase 2: Rate Limiter Hardening

**Goal**: Rate limiter is production-ready with comprehensive test coverage and improved lockout strategy
**Depends on**: Phase 1 (for event pattern consistency)
**Requirements**: RATE-01, RATE-02, RATE-03, RATE-04
**Success Criteria** (what must be TRUE):

  1. AuthRateLimiter passes comprehensive test suite covering happy path and edge cases
  2. Rate limiter uses exponential backoff for lockout duration instead of fixed 300s
  3. Rate limiter emits domain events (RateLimitLockout, RateLimitUnlock) for audit trail
  4. Cleanup worker handles edge cases (concurrent cleanup, timer drift) without deadlocks or missed expirations
**Plans**: 2 plans

Plans:

- [x] 02-01-PLAN.md -- TDD: comprehensive test suite + exponential backoff lockout (RATE-01, RATE-02)
- [x] 02-02-PLAN.md -- Domain events (RateLimitLockout/Unlock) + cleanup hardening (RATE-03, RATE-04)

### Phase 3: JWT Lifetime Enforcement

**Goal**: JWT tokens have enforced maximum lifetime to prevent excessively long-lived tokens
**Depends on**: Phase 1 (for config pattern consistency)
**Requirements**: JWT-01, JWT-02, JWT-03
**Success Criteria** (what must be TRUE):

  1. JWTAuthenticator rejects tokens where (exp - iat) exceeds max_token_lifetime
  2. max_token_lifetime defaults to 3600s and is configurable via YAML and environment variable
  3. Rejected tokens produce clear error messages indicating lifetime violation (not generic auth failure)
**Plans**: 1 plan

Plans:

- [x] 03-01-PLAN.md -- TDD: JWT lifetime enforcement with configurable max_token_lifetime (JWT-01, JWT-02, JWT-03)

### Phase 4: API Key Rotation

**Goal**: API keys can be rotated with configurable grace period for zero-downtime transitions
**Depends on**: Phase 1 (understanding store modification patterns)
**Requirements**: KROT-01, KROT-02, KROT-03, KROT-04
**Success Criteria** (what must be TRUE):

  1. API key can be rotated via rotate_key() method producing new key while old remains valid
  2. Grace period is configurable (default 24h) after which old key is auto-invalidated
  3. Key rotation emits KeyRotated domain event with metadata (key_id, rotated_at, grace_until)
  4. Rotated keys are tracked in all auth stores (SQLite, Postgres, EventSourced) with grace period enforcement
  5. After grace period expires, old key is rejected and only new key is valid
**Plans**: 2 plans

Plans:

- [x] 04-01-PLAN.md -- TDD: KeyRotated event + InMemoryApiKeyStore rotation with grace period (KROT-01, KROT-02, KROT-03)
- [x] 04-02-PLAN.md -- Cross-store rotation: SQLite/Postgres/EventSourced + cross-store tests (KROT-04)

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Timing Attack Prevention | 2/2 | Complete | 2026-02-15 |
| 2. Rate Limiter Hardening | 2/2 | Complete | 2026-02-15 |
| 3. JWT Lifetime Enforcement | 1/1 | Complete | 2026-02-15 |
| 4. API Key Rotation | 2/2 | Complete | 2026-02-15 |

---
*Created: 2026-02-15*
*Last updated: 2026-02-15 after Phase 4 execution complete*
