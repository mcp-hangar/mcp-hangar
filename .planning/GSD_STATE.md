# GSD State

> Current working state for the project. Updated as milestones progress.

## Current Focus

- **Milestone:** v0.13.0 -- Kubernetes Enforcement Foundation + Licensing
- **Target date:** 2026-04-15
- **Active phase:** Phase 33 (OTLP Completeness for Security Events) -- Plan 01 complete, Plan 02 next
- **Current version:** v0.12.0

## Active P0 Work Items

### Licensing & Structure (pre-requisite for everything else)
- [ ] Extract interfaces/contracts for enterprise features to `domain/contracts/` and `application/ports/`
- [ ] Create `enterprise/` directory structure
- [ ] Move Pro/Enterprise implementations to `enterprise/` (see MIGRATION_ENTERPRISE.md)
- [ ] Add CI import boundary check
- [ ] Add `enterprise/LICENSE.BSL`

### Kubernetes Enforcement (Phase 1 core)
- [ ] Capability declaration schema (`capabilities` config block in provider config + CRD)
- [ ] NetworkPolicy generation from capabilities (operator)
- [ ] Operator enforcement loop (capability enforcement + violation signaling)
- [ ] Admission/policy integration (reject unsafe specs before runtime)
- [ ] Runtime capability verification (declared vs observed)
- [ ] Violation signals (denied egress, capability drift, policy rejection)

### OTEL (cross-cutting)
- [x] MCP-aware OTEL semantic conventions -- Plan 31-01 complete (conventions wired into tracing.py, set_governance_attributes helper added)
- [x] MCP-aware OTEL semantic conventions -- Plan 31-02 complete (OTEL span added to TracedProviderService.invoke_tool)
- [x] MCP-aware OTEL semantic conventions -- Plan 31-03 complete (InMemorySpanExporter integration test, 6 real SDK tests)
- [x] End-to-end trace context propagation (agent -> Hangar -> provider) -- Phase 32 complete (3/3 plans: inbound extraction, outbound injection, e2e test)
- [x] OTLP audit exporter -- Plan 33-01 complete (IAuditExporter port, OTLPAuditExporter, NullAuditExporter, 5 tests)

### Hardening
- [ ] CI security scanning (Trivy/Grype on images, Semgrep on source)
- [ ] Dependency audit (`pip-audit`, `npm audit`, SBOM)
- [ ] Auth stack test coverage audit (target 90%+)

## Blocked / Waiting

(none currently)

## Recently Completed

- Phase 33-01: IAuditExporter port + OTLPAuditExporter infrastructure adapter for OTLP log record export of security events (2026-03-24)
- Phase 32 complete: all 3 plans delivered -- extract_trace_context in BatchExecutor, inject_trace_context in HttpClient, e2e InMemorySpanExporter test (2026-03-24)
- Phase 32-02: inject_trace_context wired into HttpClient.call() for outbound W3C TraceContext propagation (2026-03-24)
- Phase 32-01: extract_trace_context wired into BatchExecutor._execute_call, CallSpec.metadata field added (2026-03-24)
- Phase 31 complete: all 3 plans delivered -- conventions wired, OTEL span in TracedProviderService, InMemorySpanExporter integration test (34 total tests) (2026-03-24)
- Phase 31-02: OTEL span added to TracedProviderService.invoke_tool with governance attributes (2026-03-24)
- Phase 31-01: Wired conventions.py constants into tracing.py, added set_governance_attributes() helper (2026-03-24)
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
| v6.0 | v0.13.0 / v0.14.0 | OpenTelemetry Foundation | `.planning/milestones/v6.0-otel-foundation-ROADMAP.md` |
| v7.0 | v0.13.0 | K8s Enforcement + Licensing | `.planning/milestones/v7.0-k8s-enforcement-licensing-ROADMAP.md` |
| v8.0 | v0.14.0 | Behavioral Profiling Alpha | `.planning/milestones/v8.0-behavioral-profiling-ROADMAP.md` |
| v9.0 | v0.15.0 | Identity Propagation & Audit | `.planning/milestones/v9.0-identity-audit-ROADMAP.md` |
| v10.0 | v0.16.0 | Semantic Analysis Alpha | `.planning/milestones/v10.0-semantic-analysis-ROADMAP.md` |
| v11.0 | v1.0.0 + v1.x | H2 2026 + Production Release | `.planning/milestones/v11.0-h2-exploration-ROADMAP.md` |
