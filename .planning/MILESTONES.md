# Milestones

## v0.9 Security Hardening (Shipped: 2026-02-15)

**Phases completed:** 4 phases, 7 plans
**Timeline:** 2026-02-15 (single day, 0.61 hours execution time)
**Files changed:** 30 files, +5012/-55 lines

**Key accomplishments:**

- Constant-time API key validation (hmac.compare_digest) across all 4 auth stores, eliminating timing side-channel attacks
- Exponential backoff rate limiting (2x escalation, capped at 1h) with RateLimitLockout/Unlock domain events for audit trail
- JWT max token lifetime enforcement (configurable, default 3600s) with specific TokenLifetimeExceededError messages
- Zero-downtime API key rotation with configurable grace period (default 24h) across InMemory, SQLite, Postgres, and EventSourced stores

**Archive:** `.planning/milestones/v0.9-ROADMAP.md`, `.planning/milestones/v0.9-REQUIREMENTS.md`

---
