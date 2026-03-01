# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v0.10 -- Documentation & Kubernetes Maturity

**Shipped:** 2026-03-01
**Phases:** 3 | **Plans:** 6 | **Sessions:** 4

### What Was Built

- Configuration Reference page (523 lines, 13 YAML sections, 28+ env vars) and MCP Tools Reference page (897 lines, 22 tools, 7 categories)
- Provider Groups Guide (355 lines, 5 strategies, health policies, circuit breaker, tool filtering) and Facade API Guide (430 lines, tabbed async/sync, HangarConfig builder)
- MCPProviderGroup controller (read-only aggregation, label selection, threshold-based health, 3 independent conditions)
- MCPDiscoverySource controller (4 discovery modes, additive/authoritative sync, owner references, partial failure tolerance)
- envtest integration test suite (12 tests: 6 group + 6 discovery) with TestMain-based setup
- Both Helm charts synchronized to v0.10.0 with NOTES.txt and test templates

### What Worked

- Phase research before planning consistently produced focused, well-scoped plans with minimal rework
- Atomic task commits with pre-commit hooks caught formatting issues early (markdownlint MD046, check-yaml excludes)
- Read-only aggregation pattern for MCPProviderGroup kept complexity low while delivering full status visibility
- Scoped authoritative deletion (only delete from successfully-scanned sources) provided safety-first semantics without overcomplicating sync
- envtest with testify (no Ginkgo/Gomega) kept tests readable and consistent with project convention
- Documentation plans completed in ~4 minutes each -- structured card format (params, returns, side effects) made content generation systematic

### What Was Inefficient

- `summary-extract --fields one_liner` returned null for all summaries during milestone completion -- the one_liner field wasn't populated in frontmatter, requiring manual extraction from `provides:` fields
- Milestone completion workflow has many sequential steps that could be parallelized (retrospective + state update + roadmap reorganization are independent)
- Phase 6 Plan 3 (envtest) took ~67 minutes vs 2-3 minutes for other plans -- conflict errors in annotation-triggered reconcile tests required debugging and retry patterns

### Patterns Established

- Tool card format: description, parameters table, side effects, returns table, JSON example
- Config section format: heading, description, YAML snippet, key/type/default/range table
- Tabbed async/sync pattern with `markdownlint-disable MD046` for pymdownx.tabbed compatibility
- Read-only aggregation controller pattern: select by label, aggregate status, evaluate thresholds
- Three independent conditions (Ready/Degraded/Available) with distinct semantics
- Discovery mode dispatch: switch on spec.Type, each mode returns (map, errors) independently
- Scoped deletion: authoritative sync only deletes providers from successfully-scanned sources
- Helm test pattern: busybox wget --spider to service endpoint with timeout
- NOTES.txt pattern: static text with Release/Values template refs only

### Key Lessons

1. When documentation covers internal APIs, use source code as authority over planning documents (7 tool categories from source files beat 6 from CONTEXT.md)
2. envtest conflict errors are common when tests trigger reconciliation while also modifying the same resource -- always use `require.Eventually` with retry for annotation/status updates
3. Helm template YAML files will always fail generic YAML linters -- maintain pre-commit exclude patterns proactively when adding new chart template directories
4. Static NOTES.txt content (no Go conditionals) is simpler to maintain and test than dynamic templates for early-stage charts

### Cost Observations

- Sessions: 4
- Notable: Documentation phases executed fastest (~4min each), Kubernetes controller phases required more debugging time for envtest integration

---

## Milestone: v0.9 -- Security Hardening

**Shipped:** 2026-02-15
**Phases:** 4 | **Plans:** 7 | **Sessions:** ~3

### What Was Built

- Constant-time API key validation (hmac.compare_digest) across all 4 auth stores
- Exponential backoff rate limiting (2x escalation, capped at 1h) with domain events
- JWT max token lifetime enforcement (configurable, default 3600s)
- Zero-downtime API key rotation with grace period across InMemory, SQLite, Postgres, and EventSourced stores

### What Worked

- Small, focused phases (1-2 plans each) completed rapidly -- 0.78 hours total execution
- Security audit document (AUTH_SECURITY_AUDIT.md) provided clear gap analysis that directly mapped to phases
- Domain event pattern (RateLimitLockout/Unlock, KeyRotated) kept audit trail consistent with existing architecture
- Value object pattern (max_token_lifetime=0 as escape hatch) provided clean API design

### What Was Inefficient

- Phase 4 (API Key Rotation) took longest (14.3min, 7.2min/plan avg) compared to others (~3-4min avg) -- cross-store coordination inherently more complex
- Some plans overlapped in touching the same files (rate_limiter.py modified in both Phase 2 plans)

### Patterns Established

- hmac.compare_digest for ALL hash comparisons (not just API keys)
- Iterate all dict entries without early exit to prevent timing side-channels
- Dummy hash comparison for SQL stores as defense-in-depth
- event_publisher optional callback pattern for backward-compatible event emission
- Cascading rotation prevention (block rotate if grace period active)

### Key Lessons

1. Security hardening is best done as a focused milestone with audit-driven scope -- the AUTH_SECURITY_AUDIT.md made phase decomposition trivial
2. Cross-store concerns (rotation across InMemory/SQLite/Postgres/EventSourced) multiply testing surface -- budget extra time
3. Escape hatches (max_token_lifetime=0 disables check) should be explicit design decisions, not afterthoughts

### Cost Observations

- Sessions: ~3
- Notable: Fastest milestone to date -- 0.78 hours total. Small focused phases with clear security audit guidance eliminated ambiguity

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Sessions | Phases | Plans | Key Change |
|-----------|----------|--------|-------|------------|
| v0.9 | ~3 | 4 | 7 | Audit-driven scope, rapid execution |
| v0.10 | 4 | 3 | 6 | Research-first planning, mixed Go/Python/Docs work |

### Velocity

| Milestone | Total Time | Files Changed | Lines Added | Avg Plan Duration |
|-----------|-----------|---------------|-------------|-------------------|
| v0.9 | 0.78h | 30 | +5,012 | 4.7min |
| v0.10 | ~2 days | 40 | +8,766 | varies (2-67min) |

### Top Lessons (Verified Across Milestones)

1. Focused phases with clear scope (audit or research-driven) execute fastest with least rework
2. Domain event emission for all state changes provides consistent audit trail and enables future features (monitoring, alerting) without retrofitting
3. Pre-commit hooks catch issues early but require proactive exclude pattern maintenance when adding new file types or directories
