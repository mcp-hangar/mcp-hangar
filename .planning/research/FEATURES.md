# Feature Landscape

**Domain:** Documentation, Kubernetes operator, and Helm chart maturity for a production-grade MCP infrastructure platform
**Researched:** 2026-02-28
**Confidence:** HIGH (based on direct codebase analysis + established patterns from mature K8s operators like cert-manager, Prometheus Operator, Istio, ArgoCD, and CNCF documentation standards)

---

## Table Stakes

Features users expect for enterprise adoption. Missing = product feels incomplete or untrusted.

### Documentation

| Feature | Why Expected | Complexity | Dependencies | Notes |
|---------|--------------|------------|--------------|-------|
| Configuration Reference (full YAML schema) | Enterprise users need exhaustive config docs to deploy without reading source code. Every major infra tool (Prometheus, Grafana, ArgoCD) ships one. | Med | Existing config.py, value_objects | Must cover all YAML keys, env vars, defaults, validation rules, and examples. Currently missing entirely -- only `reference/cli.md` and `reference/hot-reload.md` exist. |
| MCP Tools Reference (all 22 tools) | Users calling tools via MCP protocol need parameter names, types, return schemas, error codes, and side effects documented. Like an API reference. | Med | Existing tool implementations in `server/tools/` | Currently zero tool reference pages. The 22 tools are the primary API surface -- undocumented API is a non-starter for enterprise. |
| Fix broken documentation links | Broken links (29 identified) signal unmaintained docs. Enterprise evaluators check for this. `docs/security/AUTH_SECURITY_AUDIT.md` referenced in mkdocs.yml but directory does not exist. | Low | All existing docs | mkdocs.yml references `security/AUTH_SECURITY_AUDIT.md` but `docs/security/` directory is missing entirely. Stale org name `mapyr` in mkdocs.yml repo_url. |
| Provider Groups Guide | Provider groups are a core differentiator (load balancing, failover, circuit breaker). Undocumented features are invisible features. | Med | Existing group implementation | Currently no guide for groups despite having 5 LB strategies, health policies, and circuit breaker. |
| Facade API Guide (Hangar/SyncHangar) | Programmatic users need to know the public API surface for embedding MCP Hangar in applications and frameworks. | Med | Existing facade implementation | No docs exist for the programmatic API. Enterprise devs integrating Hangar into their apps have no starting point. |

### Kubernetes Operator

| Feature | Why Expected | Complexity | Dependencies | Notes |
|---------|--------------|------------|--------------|-------|
| MCPProviderGroup controller | CRD types defined, no controller exists. Groups are central to the domain model. An operator managing individual providers but not groups is like a Deployment controller without ReplicaSets. | High | MCPProvider controller (done), group CRD types (done) | Must implement label-based MCPProvider selection, status aggregation (ready/degraded/dead counts), health policy evaluation, and condition reporting. |
| MCPDiscoverySource controller | CRD types defined, no controller exists. Discovery is table stakes for K8s-native infrastructure -- operators should react to cluster state, not require manual CR creation. | High | MCPProvider controller (done), discovery CRD types (done) | Must implement 4 discovery modes (Namespace, ConfigMap, Annotations, ServiceDiscovery), additive vs authoritative sync, refresh intervals, and provider template application. |
| Controller tests | The existing MCPProvider controller has tests. New controllers without tests are unshippable for production-grade claims. | Med | Each new controller | Standard controller-runtime envtest pattern. |
| Status conditions on all CRDs | Already implemented on MCPProvider (Ready, Progressing, Degraded, Available). New controllers must follow same pattern. | Low | Part of each controller | Standard K8s condition types for observability. |

### Helm Charts

| Feature | Why Expected | Complexity | Dependencies | Notes |
|---------|--------------|------------|--------------|-------|
| Version sync (0.2.0 to 0.10.0) | Charts at 0.2.0 while app is at 0.10.0 signals abandonment. Enterprise users check chart versions against app versions. | Low | Chart.yaml updates, values.yaml review | Both `mcp-hangar` and `mcp-hangar-operator` charts stuck at 0.2.0. Must update Chart.yaml `version` and `appVersion`. |
| NOTES.txt post-install instructions | Every serious Helm chart prints useful info after install (endpoints, next steps, common commands). Currently missing from both charts. | Low | Template values | Standard practice: show service URL, how to check status, link to docs. Both charts lack NOTES.txt entirely. |
| Helm test templates | `helm test` is the standard way to verify chart installation. Missing tests = no CI/CD validation path. Enterprise users expect `helm test <release>` to work. | Med | Running pods, service endpoints | Need connection test pods that verify the operator/server are reachable and healthy. |

---

## Differentiators

Features that set MCP Hangar apart. Not expected, but valued by enterprise adopters.

| Feature | Value Proposition | Complexity | Dependencies | Notes |
|---------|-------------------|------------|--------------|-------|
| CRD API Reference (auto-generated) | Auto-generated CRD reference from Go types. Most K8s operators (cert-manager, Istio) publish complete CRD field docs. Reduces support burden. | Med | CRD Go types in `api/v1alpha1/` | Could use `crd-ref-docs` or `gen-crd-api-reference-docs` tools. Not blocking for v0.10, but high value. |
| Troubleshooting guide / runbook for operators | Production operators ship runbooks: "provider stuck in Initializing", "discovery not finding services", "group health degraded". Reduces on-call burden. | Med | Understanding of failure modes | Only `runbooks/RELEASE.md` exists today. Operational runbooks are a strong differentiator for enterprise adoption. |
| MCPProviderGroup controller with automatic rebalancing | When group members change (provider added/removed), controller automatically rebalances. Goes beyond basic label-based selection. | High | MCPProviderGroup controller | Maps to existing domain saga pattern (GroupRebalanceSaga). Could be deferred to v0.11. |
| Discovery source event emission | Controller emits K8s Events and domain events when providers are discovered/created/removed. Enables audit trail in K8s-native way. | Med | MCPDiscoverySource controller | Matches existing domain event pattern. Standard operator practice. |
| Helm chart values schema (values.schema.json) | JSON Schema for values.yaml enables IDE validation and `helm lint` enforcement. Cert-manager, Istio, and ArgoCD ship these. | Med | Existing values.yaml | Not table stakes yet for v0.10, but signals chart maturity. |
| Architecture Decision Records for K8s design choices | Document why controllers are designed the way they are. ADR-001 for Langfuse exists; need ADRs for operator design decisions. | Low | Design decisions | Helps future contributors understand tradeoffs. |

---

## Anti-Features

Features to explicitly NOT build in v0.10. Either premature, out of scope, or actively harmful.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| Webhook admission controller for CRDs | Adds operational complexity (TLS cert management, webhook availability requirements). CRD validation via kubebuilder markers is sufficient at this stage. | Use kubebuilder validation markers (already in place) and CEL validation expressions in CRDs. |
| CRD version conversion (v1alpha1 to v1beta1) | Premature. API is not stable yet. Converting between versions adds maintenance burden without users needing it. | Keep v1alpha1 until API stabilizes. The `crds.conversion.enabled: false` in values.yaml is correct. |
| Multi-cluster operator support | Massive complexity leap. No evidence of demand. Single-cluster operator with namespace scoping is sufficient. | Document single-cluster deployment. Let users deploy one operator per cluster. |
| Operator Lifecycle Manager (OLM) packaging | OLM adoption is declining outside OpenShift. Helm is the dominant distribution mechanism. | Ship Helm charts. Add OLM only if OpenShift users request it. |
| Auto-generated API docs from docstrings (Python) | mkdocstrings plugin is already configured but generating API docs for internal domain code exposes implementation details. | Document the public API surface (Hangar/SyncHangar facade) manually. Keep domain internals private. |
| Helm umbrella chart combining server + operator | Couples lifecycle of two independent components. Operator may be updated independently of server. | Ship two separate charts (already the case). Document deployment order in guides. |
| Discovery controller managing cross-namespace resources | Security concern. Discovery in namespace A creating providers in namespace B requires elevated RBAC and violates least-privilege. | Discovery creates MCPProviders in same namespace as MCPDiscoverySource. Document cross-namespace patterns as manual. |

---

## Feature Dependencies

```
Fix broken docs links ──► (unblocks all other docs, readers can navigate)
         │
         ├──► Configuration Reference
         │         │
         │         └──► Provider Groups Guide (references config syntax)
         │         └──► Facade API Guide (references config syntax)
         │
         └──► MCP Tools Reference (standalone, references no other new pages)

MCPProvider controller (DONE) ──► MCPProviderGroup controller
         │                              │
         │                              └──► Group health policy evaluation
         │                              └──► Label-based provider selection
         │
         └──► MCPDiscoverySource controller
                  │
                  └──► Provider template application
                  └──► Additive vs authoritative sync

Helm version sync ──► NOTES.txt (references correct versions)
         │
         └──► Helm test templates (tests correct endpoints)
```

**Critical path:** Fix broken docs links must come first because all new doc pages will link to existing pages. MCPProviderGroup and MCPDiscoverySource controllers are independent of each other but both depend on the existing MCPProvider controller. Helm version sync must precede NOTES.txt and test templates.

---

## Prioritization Matrix

### P0 -- Must ship in v0.10 (enterprise adoption blockers)

| Feature | Category | Complexity | Rationale |
|---------|----------|------------|-----------|
| Fix 29 broken documentation links | Docs | Low | Foundational. Broken links undermine all other doc efforts. Includes fixing missing `docs/security/` directory and stale `mapyr` org name. |
| Configuration Reference | Docs | Med | Users cannot deploy without knowing config options. Currently zero config reference exists. |
| MCP Tools Reference | Docs | Med | The 22 MCP tools are the entire user-facing API. Undocumented API = unusable product for new users. |
| Helm version sync (0.2.0 to 0.10.0) | Helm | Low | Version mismatch signals abandoned charts. Quick fix with high signal value. |
| MCPProviderGroup controller | K8s | High | CRD types defined, no controller. Incomplete operator is worse than no operator -- implies broken product. |
| MCPDiscoverySource controller | K8s | High | Same as above. CRD exists without controller = unusable custom resource. |

### P1 -- Should ship in v0.10 (significantly improves experience)

| Feature | Category | Complexity | Rationale |
|---------|----------|------------|-----------|
| Provider Groups Guide | Docs | Med | Groups are a differentiator. Without docs, the feature is invisible. |
| Facade API Guide | Docs | Med | Programmatic users need this to integrate Hangar into their applications. |
| NOTES.txt post-install instructions | Helm | Low | Quick win. Users expect post-install guidance from `helm install`. |
| Helm test templates | Helm | Med | Enables CI/CD validation. Standard practice for production Helm charts. |
| Controller tests (Group + Discovery) | K8s | Med | Production-grade claim requires tests. Already have pattern from MCPProvider controller. |

### P2 -- Nice to have (defer to v0.11 if time-constrained)

| Feature | Category | Complexity | Rationale |
|---------|----------|------------|-----------|
| CRD API Reference (auto-generated) | Docs | Med | High value but not blocking. Can reference CRD YAML examples in K8s guide instead. |
| Troubleshooting / operational runbooks | Docs | Med | Important for production use but can iterate after initial adoption. |
| Helm values.schema.json | Helm | Med | IDE validation is nice but not blocking deployment. |
| MCPProviderGroup automatic rebalancing | K8s | High | Stretch goal. Basic label selection + status aggregation is sufficient for v0.10. |
| ADRs for operator design decisions | Docs | Low | Good practice but not user-facing. |

---

## Current State Gaps Analysis

### Documentation: What Exists vs What's Needed

| Category | Exists | Missing (Table Stakes) |
|----------|--------|----------------------|
| Getting Started | installation.md, quickstart.md | None -- adequate |
| Guides | 8 guides (auth, batch, containers, discovery, HTTP, K8s, observability, testing) | Provider Groups guide, Facade API guide |
| Reference | CLI reference, hot-reload reference | Configuration Reference, MCP Tools Reference, CRD API Reference |
| Cookbook | 4 recipes (HTTP gateway, health checks, circuit breaker, failover) | None -- adequate for v0.10 |
| Architecture | Overview, Event Sourcing, 1 ADR | None -- adequate for v0.10 |
| Runbooks | Release process | Operational troubleshooting (P2) |
| Security | security.md (policy) | AUTH_SECURITY_AUDIT.md referenced but missing |

### Kubernetes Operator: What Exists vs What's Needed

| Component | Status | Gap |
|-----------|--------|-----|
| CRD types (MCPProvider) | Done, 535 lines | None |
| CRD types (MCPProviderGroup) | Done, 264 lines | None |
| CRD types (MCPDiscoverySource) | Done, 297 lines | None |
| MCPProvider controller | Done, 535 lines, with tests | None |
| MCPProviderGroup controller | **Missing** | Full reconciler needed |
| MCPDiscoverySource controller | **Missing** | Full reconciler needed |
| Hangar client (pkg/hangar) | Done, with tests | May need new methods for group/discovery |
| Pod builder (pkg/provider) | Done, with tests | None |
| Metrics (pkg/metrics) | Done, with tests | May need new metrics for group/discovery |

### Helm Charts: What Exists vs What's Needed

| Component | Status | Gap |
|-----------|--------|-----|
| mcp-hangar chart | 0.2.0, 7 templates | Version sync to 0.10.0, missing NOTES.txt, missing tests |
| mcp-hangar-operator chart | 0.2.0, 9 templates + CRDs | Version sync to 0.10.0, missing NOTES.txt, missing tests |
| CRD templates | 3 CRD YAMLs in operator chart | May need regeneration if CRD types changed |
| values.yaml | Both charts have comprehensive values | Review for new features (group/discovery config) |

---

## Sources

- **Direct codebase analysis**: All findings based on reading actual files in the repository (HIGH confidence)
- **Kubernetes operator patterns**: Derived from established patterns in cert-manager, Prometheus Operator, ArgoCD, and Istio operators (HIGH confidence -- these are industry standard)
- **Helm chart conventions**: Based on Helm best practices documentation and patterns from major CNCF charts (HIGH confidence)
- **Documentation standards**: Based on patterns from Kubernetes, Docker, Terraform, and similar infrastructure documentation sites (HIGH confidence)
