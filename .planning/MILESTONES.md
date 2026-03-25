# Milestones

## v5.0: Platform Management Console -- COMPLETE (2026-03-23)

**Delivered:** 6 of 8 phases (23, 24, 25, 26, 28, 30)
**Descoped:** Phases 27 (RBAC Management API), 29 (Config Export Finalization)

### What was delivered

- **Provider/Group CRUD** -- Full REST API (create/update/delete) + config serializer + backup rotation
- **Discovery Source Management** -- Runtime register/deregister/update/toggle + trigger scan
- **MCP Provider Catalog** -- SQLite-backed catalog with seed data, deploy-to-provider
- **UI: Provider & Group CRUD Forms** -- Create/edit/delete drawers with form validation, member management
- **UI: Discovery Wizard + Catalog Browser** -- Multi-step source wizard, catalog grid with search/tags/deploy
- **UI: RBAC Management** -- SecurityPage with Events/Roles/Principals tabs, permission picker, tool access policy editor (MSW mocks)
- **UI: Config Export** -- Config tabs (Current/Export/Diff), YAML viewer, toast notifications, integration polish (MSW mocks)

### What was descoped

- **Phase 27**: RBAC Management API (custom role CRUD, principals REST, tool access policy endpoints) -- UI works on mocks, backend deferred
- **Phase 29**: Config Export Finalization (extended serialize_full_config, /api/config/diff, round-trip persistence) -- UI works on mocks, backend deferred

Plans for Phase 27 preserved in `.planning/phases/27-rbac-tool-access-api/`.

### Phases archived

Phases 23-30 in `.planning/phases/`.
Roadmap: `.planning/milestones/v5.0-ROADMAP.md`.

---

## v6.0: OpenTelemetry Foundation -- COMPLETE (2026-03-24)

**Delivered:** 4 of 4 phases (31, 32, 33, 34) -- 11 plans, 31 commits
**PyPI:** v0.13.0 (P0) / v0.14.0 (P1)
**Phases:** 31-34
**Stats:** 42 files changed, 3,429 insertions(+), 33 deletions(-), ~52 new tests
**Timeline:** 2026-03-24 (single day)
**Roadmap:** `.planning/milestones/v6.0-otel-foundation-ROADMAP.md`

### What was delivered

- **MCP OTEL Semantic Conventions** -- conventions.py constants wired into tracing.py, `set_governance_attributes()` helper, OTEL span on `TracedProviderService.invoke_tool()` with governance attributes
- **W3C Trace Context Propagation** -- Inbound extraction in `BatchExecutor._execute_call`, outbound injection in `HttpClient.call()`, end-to-end trace correlation verified with InMemorySpanExporter
- **OTLP Audit Pipeline** -- `IAuditExporter` port (Protocol-based), `OTLPAuditExporter` infrastructure adapter, `OTLPAuditEventHandler` bridging domain events (`ToolInvocationCompleted/Failed`, `ProviderStateChanged`) to OTLP log records, bootstrap wiring with OTLP endpoint auto-detection
- **Reference Deployments** -- OTEL Collector + Prometheus, OpenLIT (Hangar -> Collector -> OpenLIT), Langfuse (documentation-only) docker-compose recipes with CI smoke tests
- **MkDocs Documentation** -- Observability integrations page with full MCP attribute taxonomy (7 convention classes), copy-paste configuration for 4 partner backends

### Deviations (all auto-resolved)

- `CallSpec.metadata` field added (was missing, blocked trace context extraction)
- Trace injection target corrected to `HttpClient.call()` (not `HttpLauncher`)
- `OTLPAuditEventHandler` renamed (collision with existing `AuditEventHandler`)
- structlog `event=` kwarg renamed to `audit_event=` (internal collision)

### Phases archived

Phases 31-34 in `.planning/phases/`.
Roadmap: `.planning/milestones/v6.0-otel-foundation-ROADMAP.md`.

---

## v7.0: Kubernetes Enforcement Foundation + Licensing -- COMPLETE (2026-03-24)

**PyPI:** v0.13.0
**Phases:** 35-41 (7 of 7 complete)
**Roadmap:** `.planning/milestones/v7.0-k8s-enforcement-licensing-ROADMAP.md`

### Licensing track (complete)

- **Phase 35** (Extract Enterprise Contracts) -- COMPLETE (2026-03-24): 3 plans, 46 tests, 6 contracts + Null implementations
- **Phase 36** (Enterprise Directory Migration) -- COMPLETE (2026-03-24): 4 plans, 16 boundary tests, enterprise/ structure verified
- **Phase 37** (CI Import Boundary + License Verification) -- COMPLETE (2026-03-24): 2 plans, CI merge gate, dual-license docs

### K8s enforcement track (complete)

- **Phase 38** (Capability Declaration Schema) -- COMPLETE (2026-03-24): 3 plans, 40 tests, Python VO from_dict() + CRD types + example configs
- **Phase 39** (NetworkPolicy Generation) -- COMPLETE (2026-03-24): 3 plans, 21 tests (15 builder + 6 reconciler), Go NetworkPolicyBuilder + reconciler integration + Docker binary enforcement
- **Phase 40** (Operator Enforcement Loop + Violation Signals) -- COMPLETE (2026-03-24): 3 plans, 66 tests, ViolationType/ViolationSeverity VOs, Prometheus counters (Python + Go), reconcileViolationDetection, ViolationRecord CRD status, cross-language integration tests
- **Phase 41** (Admission + Runtime Capability Verification) -- COMPLETE (2026-03-24): 3 plans, 20 tests (11 Go + 9 Python), CEL admission validation, ExpectedTools CRD field, runtime drift detection, enforcement modes, saga safety net, verified 4/4 SC

---

## v8.0: Behavioral Profiling Alpha -- IN PROGRESS

**PyPI:** v0.14.0
**Phases:** 42-47 (4 of 6 complete)
**Roadmap:** `.planning/milestones/v8.0-behavioral-profiling-ROADMAP.md`

Network behavioral baseline, deviation detection, tool schema drift, resource profiling, behavioral reports, license key infrastructure, dashboard auth enforcement.

### Completed phases

- **Phase 42** (Behavioral Profiling Contracts + Core Infrastructure) -- COMPLETE (2026-03-25): 3 plans, 13 commits, 49 tests, Protocol contracts (IBehavioralProfiler/IBaselineStore/IDeviationDetector), BehavioralMode enum, NetworkObservation VO, SQLite BaselineStore, NullBehavioralProfiler, bootstrap wiring, behavioral config section, verified 4/4 SC
- **Phase 43** (Network Connection Logging Per Container) -- COMPLETE (2026-03-25): 3 plans, 9 commits, 62 tests, Docker/K8s network monitors, ConnectionLogWorker background worker, proc_net_parser, verified 4/4 SC
- **Phase 44** (Behavioral Deviation Detection) -- COMPLETE (2026-03-25): 3 plans, 7 commits, 28 tests, DeviationType enum, BehavioralDeviationDetected event, DeviationDetector (3 rules), ENFORCING refactoring, event handler bridge, verified 4/4 SC
- **Phase 45** (Tool Schema Drift Detection) -- COMPLETE (2026-03-25): 3 plans, 8 commits, 30 tests, SchemaTracker with SQLite storage, ToolSchemaChanged event, ToolSchemaChangeHandler, Prometheus counter, verified 4/4 SC

---

## v9.0: Identity Propagation & Audit -- PLANNED

**PyPI:** v0.15.0
**Phases:** 48-53
**Roadmap:** `.planning/milestones/v9.0-identity-audit-ROADMAP.md`

Caller identity propagation, identity-aware audit trail, compliance export (CEF/LEEF/JSON-lines), cost attribution (FinOps), OTEL identity/policy attributes.

---

## v10.0: Semantic Analysis Alpha -- PLANNED

**PyPI:** v0.16.0
**Phases:** 54-59
**Roadmap:** `.planning/milestones/v10.0-semantic-analysis-ROADMAP.md`

Call sequence pattern engine, pre-built detection rules (exfiltration/escalation/recon), custom rule DSL, OTEL risk taxonomy, automated response actions.

---

## v11.0: H2 2026 Exploration + v1.0.0 Production Release -- PLANNED

**PyPI:** v1.0.0 (hardening), v1.x (exploration)
**Phases:** 60-66
**Roadmap:** `.planning/milestones/v11.0-h2-exploration-ROADMAP.md`

v1.0.0 stability pass, performance benchmarks, upgrade tooling, docs site, public launch. H2 exploration items (Server Card integration, multi-cluster federation, supply chain security) are demand-gated.

---

## v1.0 (archived)

Phase planning artifacts removed (no longer relevant).
