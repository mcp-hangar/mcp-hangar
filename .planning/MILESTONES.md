# Milestones

## v0.10 Documentation & Kubernetes Maturity (Shipped: 2026-03-01)

**Phases completed:** 3 phases (5-7), 6 plans
**Timeline:** 2 days (2026-02-28 -> 2026-03-01)
**Files changed:** 40 files, +8,766/-65 lines

**Key accomplishments:**

- Configuration Reference page documenting all 13 YAML config sections and 28+ environment variables with defaults and validation rules
- MCP Tools Reference page documenting all 22 tools across 7 categories with parameters, return formats, error codes, and side effects
- Provider Groups Guide covering all 5 load balancing strategies, health policies, circuit breaker, and tool access filtering with usage examples
- Facade API Guide documenting Hangar/SyncHangar public API with method signatures, HangarConfig builder, and framework integration patterns
- MCPProviderGroup Kubernetes controller with label-based selection, status aggregation, and threshold-based health policy evaluation
- MCPDiscoverySource Kubernetes controller with 4 discovery modes (Namespace, ConfigMap, Annotations, ServiceDiscovery), additive/authoritative sync, and owner references
- envtest-based integration tests for both controllers covering happy path and failure scenarios
- Both Helm charts synchronized to v0.10.0 with NOTES.txt post-install guidance and test templates for installation validation

**Archive:** `.planning/milestones/v0.10-ROADMAP.md`, `.planning/milestones/v0.10-REQUIREMENTS.md`

---

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
