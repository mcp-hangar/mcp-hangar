# Project Research Summary

**Project:** MCP Hangar v0.10 — Documentation & Kubernetes Maturity
**Domain:** Production-grade MCP provider infrastructure platform — operator controllers, documentation, Helm charts
**Researched:** 2026-02-28
**Confidence:** HIGH

## Executive Summary

MCP Hangar v0.10 is a maturity milestone, not a feature milestone. The platform already has a working Python core with DDD/CQRS/Event Sourcing, a Kubernetes operator with one controller (MCPProvider), and Helm charts at v0.2.0. What's missing: two CRD types (MCPProviderGroup, MCPDiscoverySource) have Go types defined but no controllers to reconcile them, documentation has 29 broken links and four missing reference/guide pages, and Helm charts are version-lagged at 0.2.0 vs. app 0.10.0. The work is well-scoped: implement two controllers following established patterns, fix and extend docs, and sync Helm charts.

The recommended approach is to fix documentation foundations first (broken links, org name), then implement both Kubernetes controllers with tests, and finally sync Helm charts and add remaining doc pages. The controllers are the highest-risk, highest-effort items but have well-documented patterns to follow: the existing MCPProvider controller (535 lines, with tests) serves as a direct template. All Go dependencies already exist in go.mod. No new Python dependencies are needed beyond documentation tooling (mkdocstrings-python, mkdocs-htmlproofer-plugin).

The key risks are: (1) infinite reconciliation loops between MCPProviderGroup and MCPDiscoverySource controllers when both watch MCPProvider resources, (2) finalizer deadlocks when MCPDiscoverySource owns MCPProviders that have their own finalizers, and (3) Helm CRD upgrade gaps since `helm upgrade` does not update CRDs from the `crds/` directory. All three are preventable with established patterns: MCPProviderGroup must be a read-only aggregator (never write to MCPProviders), MCPDiscoverySource must rely on Kubernetes garbage collection (not explicit child deletion in finalizers), and Helm charts need explicit CRD upgrade documentation or a pre-upgrade hook.

## Key Findings

### Recommended Stack

No new major dependencies are needed. The existing stack (Python 3.11+, Go 1.23.0, controller-runtime v0.17.0, MkDocs Material) covers everything. Stack additions are limited to documentation tooling and test infrastructure.

**Core additions:**

- **mkdocstrings-python 2.0.3**: API reference generation from Python docstrings -- uses Griffe for static analysis (no import side effects), supports Google-style docstrings matching project convention
- **mkdocs-htmlproofer-plugin 1.5.0**: Link validation in rendered HTML -- catches broken internal links, anchors, and external URLs during `mkdocs build`; actively maintained (Feb 2026)
- **helm-unittest 1.0.3**: Declarative Helm template testing -- no cluster required, fast CI feedback, tests live alongside charts
- **chart-testing (ct) 3.14.0**: Chart linting and schema validation -- official Helm project tool, superset of `helm lint`
- **envtest / ginkgo / gomega**: Already indirect dependencies in go.mod -- promote to direct for controller integration tests; no installation needed

**What NOT to use:** mkdocs-linkcheck (unmaintained since 2021), Sphinx (project committed to MkDocs), kubebuilder scaffolding (would overwrite existing customizations), Operator SDK (unnecessary abstraction), kuttl (overkill for this scope).

### Expected Features

**Must have (P0 -- enterprise adoption blockers):**

- Fix 29 broken documentation links (including missing `docs/security/` directory, stale `mapyr` org name)
- Configuration Reference page (full YAML schema, env vars, defaults, validation rules)
- MCP Tools Reference page (all 22 tools with parameters, returns, side effects)
- MCPProviderGroup controller (label-based selection, status aggregation, health policy evaluation)
- MCPDiscoverySource controller (4 discovery modes, additive/authoritative, provider template application)
- Helm chart version sync 0.2.0 to 0.10.0

**Should have (P1 -- significantly improves experience):**

- Provider Groups Guide (strategies, health policies, circuit breaker)
- Facade API Guide (Hangar/SyncHangar, HangarConfig builder)
- NOTES.txt post-install instructions for both Helm charts
- Helm test templates for CI/CD validation
- Controller tests for MCPProviderGroup and MCPDiscoverySource

**Defer to v0.11+:**

- CRD API Reference (auto-generated from Go types)
- Operational troubleshooting runbooks
- Helm values.schema.json
- MCPProviderGroup automatic rebalancing
- ADRs for operator design decisions

### Architecture Approach

The v0.10 architecture adds two new controllers to an existing controller-runtime operator. MCPProviderGroup is a read-only aggregation layer that watches MCPProviders via label selectors (like how a Service selects Pods). MCPDiscoverySource creates and manages MCPProvider custom resources as a parent using owner references. The controllers communicate through the Kubernetes API only -- no shared mutable state, no direct inter-controller calls.

**Major components:**

1. **MCPProviderGroup controller** -- selects MCPProviders via label selector, aggregates status (ready/degraded/dead counts), evaluates health policies, updates group conditions. Uses `EnqueueRequestsFromMapFunc` to watch MCPProvider changes without ownership.
2. **MCPDiscoverySource controller** -- implements 4 discovery strategies (Namespace, ConfigMap, Annotations, ServiceDiscovery) via a common interface, creates MCPProvider CRs with controller owner references, supports additive and authoritative sync modes, uses `RequeueAfter` for periodic rescan.
3. **Discovery strategies package** (`pkg/discovery/`) -- strategy interface + 4 implementations, keeping scanning logic separate from controller lifecycle management.
4. **Documentation pages** -- 4 new MkDocs pages (Configuration Reference, MCP Tools Reference, Provider Groups Guide, Facade API Guide) integrated into existing nav structure.

### Critical Pitfalls

1. **Infinite reconciliation loop** -- MCPDiscoverySource creates MCPProviders, MCPProviderGroup watches them, status updates trigger re-reconciliation in a tight cycle. **Prevention:** MCPProviderGroup must never set owner references on MCPProviders; must compare computed status against current status and skip no-op updates; use `EnqueueRequestsFromMapFunc` not `Owns()`.

2. **Finalizer deadlock on deletion** -- MCPDiscoverySource owns MCPProviders with finalizers. If child finalizer cleanup fails (e.g., Hangar core down), parent finalizer blocks indefinitely, blocking namespace deletion. **Prevention:** Do not explicitly delete children in finalizer; rely on Kubernetes garbage collection; set `blockOwnerDeletion: false`; add finalizer timeout.

3. **Helm CRD upgrade gap** -- `helm upgrade` does NOT update CRDs from the `crds/` directory. Jumping from 0.2.0 to 0.10.0 means new CRD schemas are not applied. **Prevention:** Document explicit `kubectl apply -f crds/` step; consider pre-upgrade hook Job; ensure all new CRD fields have defaults.

4. **Status update conflicts (409)** -- Both MCPProvider controller and MCPProviderGroup controller access MCPProvider objects. Concurrent reads/writes cause 409 Conflict errors. **Prevention:** MCPProviderGroup must only READ MCPProvider status, never write to it; MCPProviderGroup updates only its OWN status subresource.

5. **Authoritative mode deletes user-created MCPProviders** -- Discovery controller in authoritative mode deletes any MCPProvider not in its discovered set. **Prevention:** Only delete resources with the `mcp-hangar.io/discovery-source` label matching this source; never touch unlabeled resources.

## Implications for Roadmap

Based on research, the work naturally segments into 4 phases. Phase numbering continues from v0.9 (starting at Phase 5).

### Phase 5: Documentation Foundations

**Rationale:** Fix broken links and org name references first because all new doc pages will link to existing pages. Broken foundations undermine all subsequent documentation work. This is low complexity, high impact, and unblocks everything else.
**Delivers:** Clean, navigable documentation with no broken links; correct GitHub org references throughout; `mkdocs build --strict` passes; htmlproofer plugin integrated.
**Addresses:** Fix 29 broken links (P0), stale org name (Pitfall 13), missing `docs/security/` directory.
**Avoids:** Documentation cross-reference drift (Pitfall 12), org name inconsistency (Pitfall 13).
**Stack:** mkdocs-htmlproofer-plugin 1.5.0, pyproject.toml docs dependency group.

### Phase 6: Kubernetes Controllers

**Rationale:** Both controllers are the highest-complexity, highest-risk items. They should be implemented together (they share patterns and infrastructure) but in sequence: MCPProviderGroup first (simpler, read-only aggregation), MCPDiscoverySource second (creates resources, more complex lifecycle). Controllers MUST land before documentation references them as working.
**Delivers:** Fully functional MCPProviderGroup and MCPDiscoverySource controllers with integration tests; both registered in main.go; distinct finalizer names per controller; metrics emitting.
**Addresses:** MCPProviderGroup controller (P0), MCPDiscoverySource controller (P0), controller tests (P1).
**Avoids:** Infinite reconciliation loop (Pitfall 1), finalizer deadlock (Pitfall 2), status update conflicts (Pitfall 4), authoritative mode data loss (Pitfall 6), shared finalizer names (Pitfall 9).
**Stack:** envtest, ginkgo/gomega (promote from indirect to direct deps), controller-runtime patterns.

### Phase 7: Documentation Content

**Rationale:** New reference and guide pages depend on both clean link infrastructure (Phase 5) and working controllers (Phase 6, for accurate K8s documentation). Write docs after controllers exist to avoid documenting unimplemented features (Pitfall 7).
**Delivers:** Configuration Reference, MCP Tools Reference, Provider Groups Guide, Facade API Guide; updated mkdocs.yml nav; mkdocstrings integration for Python API docs.
**Addresses:** Configuration Reference (P0), MCP Tools Reference (P0), Provider Groups Guide (P1), Facade API Guide (P1).
**Avoids:** Referencing features before controllers exist (Pitfall 7), mkdocstrings Go confusion (Pitfall 8).
**Stack:** mkdocstrings-python 2.0.3 for Facade API and Tools Reference pages.

### Phase 8: Helm Chart Maturity

**Rationale:** Helm chart sync is low complexity but depends on controllers being finalized (CRDs may need regeneration, chart templates reference controller config). NOTES.txt should include accurate CRD upgrade instructions informed by actual upgrade testing. Helm tests validate the full stack.
**Delivers:** Both charts at v0.10.0; NOTES.txt with post-install instructions and CRD upgrade guidance; Helm test templates; CRDs regenerated if needed; chart linting passes.
**Addresses:** Helm version sync (P0), NOTES.txt (P1), Helm test templates (P1).
**Avoids:** CRD field breakage on upgrade (Pitfall 3), values.yaml breaking changes (Pitfall 10).
**Stack:** helm-unittest 1.0.3, chart-testing 3.14.0.

### Phase Ordering Rationale

- **Phase 5 first** because broken links and wrong org name are foundational -- fixing them is a prerequisite for all doc work and takes minimal effort.
- **Phase 6 before Phase 7** because writing documentation about controllers that don't exist yet leads to inaccuracy (Pitfall 7) and wasted rework. Controllers inform the docs, not the other way around.
- **Phase 8 last** because Helm charts depend on finalized CRD schemas (from Phase 6) and should include documentation links (from Phase 7) in NOTES.txt. Chart version sync without controller implementation would be cosmetic.
- **Phases 5 and 6 could partially overlap** since doc fixes and controller implementation are independent codepaths (Python/MkDocs vs. Go/operator). The roadmapper may choose to parallelize.

### Research Flags

Phases likely needing deeper research during planning:

- **Phase 6 (Kubernetes Controllers):** The MCPProviderGroup cross-resource watch pattern and MCPDiscoverySource authoritative mode are the most complex items. The existing MCPProvider controller provides a template, but the cross-controller interaction patterns (Pitfalls 1, 2, 4) need careful task-level design. Research the envtest setup for multi-controller scenarios.

Phases with standard patterns (skip research-phase):

- **Phase 5 (Documentation Foundations):** Straightforward link fixing and plugin configuration. Well-documented MkDocs patterns.
- **Phase 7 (Documentation Content):** Standard MkDocs content authoring. mkdocstrings is already configured.
- **Phase 8 (Helm Chart Maturity):** Standard Helm chart practices. helm-unittest has clear documentation.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All dependencies verified against project files (go.mod, pyproject.toml, mkdocs.yml). No speculative recommendations -- everything is either already in the project or has verified versions on PyPI/GitHub. |
| Features | HIGH | Features derived from direct codebase gap analysis. Cross-referenced with established patterns from cert-manager, Prometheus Operator, ArgoCD. |
| Architecture | HIGH | Controller patterns are standard controller-runtime. Existing MCPProvider controller provides proven template. All CRD types already defined. |
| Pitfalls | HIGH | Pitfalls identified from direct code analysis (specific line numbers referenced) and well-known Kubernetes operator failure modes. Helm CRD upgrade behavior is documented Helm behavior. |

**Overall confidence:** HIGH

### Gaps to Address

- **CRD schema evolution:** The jump from 0.2.0 to 0.10.0 may have introduced CRD field changes that weren't tracked. During Phase 8, verify that existing CRD schemas in the `crds/` directory match the current Go types by running `controller-gen`. If they diverge, existing cluster resources could fail validation.
- **HangarClient group methods:** The ARCHITECTURE.md notes that `pkg/hangar/client.go` "may need RegisterGroup/SyncGroup methods." This is unresolved -- Phase 6 planning should determine if MCPProviderGroup needs to sync state with the Python core or if it's purely a Kubernetes-level aggregation.
- **Discovery strategy completeness:** Four discovery strategies are specified (Namespace, ConfigMap, Annotations, ServiceDiscovery) but their exact behaviors need to be derived from the Python core's existing discovery implementations. Phase 6 planning should cross-reference `packages/core/mcp_hangar/infrastructure/discovery/` with the Go types.
- **mkdocstrings integration depth:** The MCP Tools Reference page plans to use mkdocstrings to auto-generate from Python docstrings. The quality depends on existing docstring coverage in `server/tools/`. If docstrings are sparse, manual documentation may be needed instead.

## Sources

### Primary (HIGH confidence)

- Project source code (go.mod, pyproject.toml, mkdocs.yml, mcpprovider_controller.go, CRD types) -- exact versions, patterns, and gaps
- controller-runtime documentation and Kubebuilder book -- canonical patterns for Watches, MapFunc, owner references
- Helm documentation -- CRD lifecycle behavior during install vs. upgrade

### Secondary (MEDIUM confidence)

- GitHub releases for helm-unittest v1.0.3, chart-testing v3.14.0 -- version verification
- Patterns from cert-manager, Prometheus Operator, ArgoCD, Istio -- industry-standard operator patterns

---
*Research completed: 2026-02-28*
*Ready for roadmap: yes*
