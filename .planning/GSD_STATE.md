# GSD State

> Current working state for the project. Updated as milestones progress.

## Current Focus

- **Milestone:** v8.0 -- Behavioral Profiling Alpha
- **Target date:** 2026-05-15
- **Active phase:** Phase 45 (Tool Schema Drift Detection) -- COMPLETE, 3/3 plans
- **Last completed phase:** Phase 45 (Tool Schema Drift Detection) -- 3/3 plans, 5 commits, 30 tests, 4/4 SC verified (2026-03-25)
- **Current version:** v0.12.0
- **Last completed milestone:** v7.0 (K8s Enforcement + Licensing) -- shipped 2026-03-24

## Active P0 Work Items

### Licensing & Structure (pre-requisite for everything else)
- [x] Extract interfaces/contracts for enterprise features to `domain/contracts/` and `application/ports/`
- [x] Create `enterprise/` directory structure
- [x] Move Pro/Enterprise implementations to `enterprise/` (see MIGRATION_ENTERPRISE.md)
- [x] Add CI import boundary check
- [x] Add `enterprise/LICENSE.BSL`

### Kubernetes Enforcement (Phase 1 core)
- [x] Capability declaration schema (`capabilities` config block in provider config + CRD)
- [x] NetworkPolicy generation from capabilities (operator)
- [x] Operator enforcement loop (capability enforcement + violation signaling)
- [x] Admission/policy integration (reject unsafe specs before runtime) -- CEL wildcard egress rule + tests complete (41-01, 41-03)
- [x] Runtime capability verification (declared vs observed) -- ExpectedTools CRD field + Python drift detection + enforcement + tests complete (41-01, 41-02, 41-03)
- [x] Violation signals (denied egress, capability drift, policy rejection)

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

- **Phase 45 COMPLETE (Tool Schema Drift Detection)** -- 3 plans, 5 commits, 30 tests, 4/4 SC verified. SchemaTracker + ToolSchemaChanged event + handler + comprehensive tests (2026-03-25)
- **Phase 45 plan 03 complete** -- 30 unit tests covering SC45-1 through SC45-4 at SchemaTracker and handler levels, hash determinism, description immunity, mixed changes, provider isolation, error handling, Prometheus counter, 2 commits (2026-03-25)
- **Phase 45 plan 02 complete** -- MIT domain types + event handler: SchemaChangeType enum (ADDED/REMOVED/MODIFIED), ToolSchemaChanged per-tool domain event, Provider.get_tool_schemas() accessor, TOOL_SCHEMA_DRIFTS_TOTAL Prometheus counter, ToolSchemaChangeHandler bridging ProviderStarted to SchemaTracker with OTLP spans, bootstrap wiring, 2 commits (2026-03-25)
- **Phase 45 plan 01 complete** -- BSL SchemaTracker with SQLite storage (BaselineStore pattern), compute_schema_hash SHA-256, check_and_store drift detection (ADDED/REMOVED/MODIFIED), first-seen returns empty (SC45-4), bootstrap_schema_tracker factory, ApplicationContext.schema_tracker field, 1 commit (2026-03-25)
- **Phase 44 COMPLETE (Behavioral Deviation Detection)** -- 3 plans, 7 commits, 28 tests, 4/4 SC verified. DeviationType enum + BehavioralDeviationDetected event + DeviationDetector (3 rules: new destination, protocol drift, frequency anomaly) + ENFORCING refactoring + event handler bridge + config propagation + end-to-end verification (2026-03-25)
- **Phase 44 plan 03 complete** -- Config parsing validation + end-to-end SC44-1 through SC44-4 verification, 8 tests, stale Phase 42 ENFORCING test fixed, 2 commits (2026-03-25)
- **Phase 44 plan 02 complete** -- BehavioralProfiler ENFORCING refactoring (check-first-store-second), BehavioralDeviationEventHandler (OTLP + Prometheus bridge), bootstrap wiring with DeviationDetector + EventBus injection, 11 tests, 2 commits (2026-03-25)
- **Phase 44 plan 01 complete** -- MIT domain types (DeviationType enum, BehavioralDeviationDetected event, OTEL conventions, Prometheus counter) + BSL DeviationDetector with 3 detection rules (new destination, protocol drift, frequency anomaly), 9 tests, 3 commits (2026-03-25)
- **Phase 44 context gathered** -- 6 gray areas discussed (detection trigger, severity model, frequency thresholds, event emission, profiler refactoring, protocol drift), 44-CONTEXT.md written (2026-03-25)
- **Phase 43 (Network Connection Logging Per Container) complete** -- 3 plans, 9 commits, 62 tests (31 Docker/parser + 11 K8s + 20 worker/bootstrap), 4/4 SC verified (2026-03-25)
- Phase 43 plan 03 complete -- ConnectionLogWorker background worker + bootstrap wiring: daemon thread orchestrating Docker/K8s monitors, feeds observations to IBehavioralProfiler, config-driven factory with graceful degradation, 20 tests (2026-03-25)
- Phase 43 plan 01 complete -- Docker network monitor: proc_net_parser (2 pure parsers), DockerNetworkMonitor (ss/proc fallback + caching), container label injection in Docker/Container launchers, 31 tests (2026-03-25)
- Phase 43 plan 02 complete -- K8sNetworkMonitor with audit events + pod exec fallback, 11 tests, lazy import for proc_net_parser (2026-03-25)
- **Phase 42 (Behavioral Profiling Contracts + Core Infrastructure) complete** -- 3 plans, 13 commits, 49 tests (26 contract + 9 BaselineStore + 14 bootstrap), 4/4 SC verified (2026-03-25)
- Phase 42 plan 03 complete -- BSL BehavioralProfiler facade + bootstrap wiring: try/except ImportError conditional loading, ApplicationContext.behavioral_profiler field, NullBehavioralProfiler fallback, 14 tests (2026-03-25)
- **Phase 42 plan 02 complete** -- BSL SQLite-backed BaselineStore: UPSERT observation aggregation, BehavioralMode persistence, thread-safe Lock, 9 tests (2026-03-25)
- **Phase 42 plan 01 complete** -- MIT behavioral profiling contracts: IBehavioralProfiler, IBaselineStore, IDeviationDetector Protocols, BehavioralMode enum, NetworkObservation VO, BehavioralModeChanged event, NullBehavioralProfiler, 26 tests (2026-03-25)
- **Phase 42 context gathered** -- 4 gray areas discussed (mode transitions, observation data model, contract scope, license gating), 42-CONTEXT.md written (2026-03-25)
- **Phase 41 (Admission + Runtime Capability Verification) complete** -- 3 plans, 20 Go tests (7 CEL admission + 1 envtest + 3 egress audit + 9 from earlier plans) + 9 Python tests (7 drift + 2 saga), 4/4 SC verified (2026-03-24)
- Phase 41 plan 03 complete: Go CEL admission validation + egress audit tests (11 tests), Python runtime drift + saga filter tests (9 tests) (2026-03-24)
- Phase 41 plan 02 complete: expected_tools on ToolCapabilities, _verify_capability_drift() in Provider aggregate, alert/block enforcement wiring, saga capability_violation: filter (2026-03-24)
- Phase 41 plan 01 complete: CEL XValidation wildcard egress rule, ExpectedTools CRD field, reconcileEgressAudit Warning event (2026-03-24)
- **Phase 40 (Operator Enforcement Loop + Violation Signals) complete** -- 3 plans, 66 tests (28 unit Python + 15 integration Python + 12 unit Go + 11 integration Go), verified 4/4 SC (2026-03-24)
- Phase 40 plan 01 complete: ViolationType/ViolationSeverity VOs, ProviderCapabilityQuarantined rename, severity field on CapabilityViolationDetected, OTEL VIOLATION_SEVERITY attribute, Prometheus counter + MetricsEventHandler bridge (2026-03-24)
- Phase 40 plan 02 complete: ViolationRecord CRD struct, CapabilityViolationsTotal Go metric, reconcileViolationDetection (NP drift + tool drift), ring-buffer capping (2026-03-24)
- Phase 40 plan 03 complete: Cross-language integration tests -- Python violation signal chain (15 tests) + Go violation detection round-trip (11 tests) (2026-03-24)
- Phase 39 plan 03 complete: Reconciler integration -- reconcileNetworkPolicy with CRUD lifecycle, OwnerReference GC, 6 fake-client tests (2026-03-24)
- Phase 39 plan 01 complete: NetworkPolicyBuilder pure function -- TDD with 15 tests, default-deny egress + DNS/loopback/CIDR/host-only rules (2026-03-24)
- Phase 39 plan 02 complete: Docker capabilities-aware network mode -- binary deny/allow enforcement via egress rules, 8 unit tests (2026-03-24)
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
| v7.0 | v0.13.0 | K8s Enforcement + Licensing (COMPLETE) | `.planning/milestones/v7.0-k8s-enforcement-licensing-ROADMAP.md` |
| v8.0 | v0.14.0 | Behavioral Profiling Alpha | `.planning/milestones/v8.0-behavioral-profiling-ROADMAP.md` |
| v9.0 | v0.15.0 | Identity Propagation & Audit | `.planning/milestones/v9.0-identity-audit-ROADMAP.md` |
| v10.0 | v0.16.0 | Semantic Analysis Alpha | `.planning/milestones/v10.0-semantic-analysis-ROADMAP.md` |
| v11.0 | v1.0.0 + v1.x | H2 2026 + Production Release | `.planning/milestones/v11.0-h2-exploration-ROADMAP.md` |
