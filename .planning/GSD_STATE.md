# GSD State

> Current working state for the project. Updated as milestones progress.

## Current Focus

- **Milestone:** v7.0 -- Kubernetes Enforcement Foundation + Licensing
- **Target date:** 2026-04-15
- **Active phase:** Phase 38 (Capability Declaration Schema) -- plan 03 of 03 complete (phase complete)
- **Current version:** v0.12.0
- **Last completed milestone:** v6.0 (OTEL Foundation) -- shipped 2026-03-24

## Active P0 Work Items

### Licensing & Structure (pre-requisite for everything else)
- [x] Extract interfaces/contracts for enterprise features to `domain/contracts/` and `application/ports/`
- [x] Create `enterprise/` directory structure
- [x] Move Pro/Enterprise implementations to `enterprise/` (see MIGRATION_ENTERPRISE.md)
- [x] Add CI import boundary check
- [x] Add `enterprise/LICENSE.BSL`

### Kubernetes Enforcement (Phase 1 core)
- [x] Capability declaration schema (`capabilities` config block in provider config + CRD)
- [ ] NetworkPolicy generation from capabilities (operator)
- [ ] Operator enforcement loop (capability enforcement + violation signaling)
- [ ] Admission/policy integration (reject unsafe specs before runtime)
- [ ] Runtime capability verification (declared vs observed)
- [ ] Violation signals (denied egress, capability drift, policy rejection)

### OTEL (cross-cutting) -- COMPLETE (Milestone v6.0, shipped 2026-03-24)
- [x] MCP-aware OTEL semantic conventions (Phases 31)
- [x] End-to-end trace context propagation (Phase 32)
- [x] OTLP audit pipeline for security events (Phase 33)
- [x] Integration recipes and reference deployments (Phase 34)

### Hardening
- [ ] CI security scanning (Trivy/Grype on images, Semgrep on source)
- [ ] Dependency audit (`pip-audit`, `npm audit`, SBOM)
- [ ] Auth stack test coverage audit (target 90%+)

## Blocked / Waiting

(none currently)

## Recently Completed

- **Phase 38 (Capability Declaration Schema) complete** -- 3 plans, 40 tests, Python VOs + CRD types + examples (2026-03-24)
- Phase 38 plan 03 complete: from_dict round-trip tests, ProviderConfig integration tests, ConfigurationError boundary tests, quickstart + K8s example configs (2026-03-24)
- Phase 38 plan 02 complete: MCPProvider CRD ProviderCapabilities Go structs + reconciler spec-to-status propagation (2026-03-24)
- Phase 38 plan 01 complete: ProviderCapabilities.from_dict() factory, capabilities wired into Provider aggregate + ProviderConfig + server/config.py (2026-03-24)
- Phase 37 verified: 4/4 success criteria PASS -- CI boundary enforcement + license documentation complete (2026-03-24)
- Phase 37 plan 01 complete: Enterprise import boundary check added to pr-validation.yml as universal merge gate (2026-03-24)
- Phase 37 plan 02 complete: License documentation -- README, CONTRIBUTING, pyproject.toml updated for dual-license model (2026-03-24)
- **Phase 36 (Enterprise Directory Migration) shipped** -- 4 plans, 2 commits, 16 boundary tests, 4/4 success criteria verified (2026-03-24)
- Phase 36 plan 04 complete: Bootstrap enterprise boundary tests -- 16 tests proving conditional loading works (2026-03-24)
- Phase 36 plan 03 complete: Persistence/observability migration audit -- all shims and bootstrap fallbacks verified correct (2026-03-24)
- Phase 36 plan 02 complete: Auth migration verification -- all 6 shim modules verified correct, zero stale imports (2026-03-24)
- Phase 36 plan 01 complete: Enterprise directory structure verified, BSL docstrings added to all placeholder modules (2026-03-24)
- **Phase 35 (Extract Enterprise Contracts) shipped** -- 3 plans, 5 commits, 46 tests (2026-03-24)
- Phase 35 complete: 46 unit tests verifying all enterprise contract Null implementations (2026-03-24)
- Phase 35 plan 02 complete: 6 Null Object implementations for enterprise contracts (2026-03-24)
- Phase 35 plan 01 complete: IToolAccessPolicyEnforcer + IDurableEventStore contracts defined (2026-03-24)
- **Milestone v6.0 (OTEL Foundation) shipped** -- 4 phases, 11 plans, 31 commits, 3,429 LOC added (2026-03-24)
- Phase 34 complete: OpenLIT/Langfuse recipes, MkDocs OTEL integrations page (2026-03-24)
- Phase 33 complete: IAuditExporter port, OTLPAuditExporter, event handler wiring, OTEL Collector reference deployment (2026-03-24)
- Phase 32 complete: W3C TraceContext extraction (inbound) + injection (outbound) + e2e test (2026-03-24)
- Phase 31 complete: conventions wired, OTEL span in TracedProviderService, InMemorySpanExporter integration test (2026-03-24)
- v0.12.0 released (Catalog API experimental, fuzz tests, config export)

## References

- Version plan: `.planning/VERSION_PLAN.md`
- Phase details: `.planning/PHASES.md`
- Migration plan: `.planning/MIGRATION_ENTERPRISE.md`
- Hardening: `.planning/HARDENING.md`
- Decisions: `.planning/DECISIONS.md`
- Product architecture: `docs/internal/PRODUCT_ARCHITECTURE.md`
- Public roadmap: `ROADMAP.md`

### Milestone files (v6.0 -- v11.0)

| Milestone | PyPI | Theme | File |
|-----------|------|-------|------|
| v6.0 | v0.13.0 / v0.14.0 | OpenTelemetry Foundation (COMPLETE) | `.planning/milestones/v6.0-otel-foundation-ROADMAP.md` |
| v7.0 | v0.13.0 | K8s Enforcement + Licensing | `.planning/milestones/v7.0-k8s-enforcement-licensing-ROADMAP.md` |
| v8.0 | v0.14.0 | Behavioral Profiling Alpha | `.planning/milestones/v8.0-behavioral-profiling-ROADMAP.md` |
| v9.0 | v0.15.0 | Identity Propagation & Audit | `.planning/milestones/v9.0-identity-audit-ROADMAP.md` |
| v10.0 | v0.16.0 | Semantic Analysis Alpha | `.planning/milestones/v10.0-semantic-analysis-ROADMAP.md` |
| v11.0 | v1.0.0 + v1.x | H2 2026 + Production Release | `.planning/milestones/v11.0-h2-exploration-ROADMAP.md` |
