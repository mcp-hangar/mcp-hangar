# Requirements: MCP Hangar

**Defined:** 2026-02-28
**Core Value:** Reliable, observable MCP provider management with production-grade lifecycle control

## v0.10 Requirements

Requirements for milestone v0.10 Documentation & Kubernetes Maturity.

### Documentation Content

- [x] **DOC-01**: Configuration Reference page documents full YAML schema with all keys, defaults, and validation rules
- [x] **DOC-02**: Configuration Reference page lists all environment variables with descriptions and examples
- [x] **DOC-03**: MCP Tools Reference page documents all 22 tools with parameters, return formats, and error codes
- [x] **DOC-04**: MCP Tools Reference page documents side effects and state changes for each tool
- [ ] **DOC-05**: Provider Groups Guide covers all 5 load balancing strategies with usage examples
- [ ] **DOC-06**: Provider Groups Guide covers health policies, circuit breaker, and tool access filtering
- [ ] **DOC-07**: Facade API Guide documents Hangar/SyncHangar public API with method signatures
- [ ] **DOC-08**: Facade API Guide covers HangarConfig builder and framework integration patterns

### Kubernetes Controllers

- [ ] **K8S-01**: MCPProviderGroup controller reconciles groups with label-based MCPProvider selection
- [ ] **K8S-02**: MCPProviderGroup controller aggregates member status (ready/degraded/dead counts)
- [ ] **K8S-03**: MCPProviderGroup controller evaluates health policies and reports conditions
- [ ] **K8S-04**: MCPDiscoverySource controller implements 4 discovery modes (Namespace, ConfigMap, Annotations, ServiceDiscovery)
- [ ] **K8S-05**: MCPDiscoverySource controller supports additive and authoritative sync modes
- [ ] **K8S-06**: MCPDiscoverySource controller creates MCPProvider CRs with owner references and provider templates
- [ ] **K8S-07**: Both controllers have envtest-based integration tests covering happy path and failure scenarios

### Helm Charts

- [ ] **HELM-01**: Both charts (mcp-hangar, mcp-hangar-operator) updated to version 0.10.0
- [ ] **HELM-02**: Both charts include NOTES.txt with post-install instructions (endpoints, status commands, docs links)
- [ ] **HELM-03**: Both charts include Helm test templates for installation validation

## Future Requirements

Deferred to v0.11+. Tracked but not in current roadmap.

### Documentation

- **DEFER-01**: Fix 29 broken documentation links and stale references across 8 files
- **DEFER-02**: CRD API Reference auto-generated from Go types
- **DEFER-03**: Troubleshooting and operational runbooks for production deployments

### Helm Charts

- **DEFER-04**: Helm values.schema.json for IDE validation and helm lint enforcement
- **DEFER-05**: MCPProviderGroup automatic rebalancing on member changes

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Webhook admission controller | Premature -- kubebuilder validation markers sufficient, TLS cert management adds complexity |
| CRD version conversion (v1alpha1 to v1beta1) | API not stable yet, version conversion adds maintenance burden without demand |
| Multi-cluster operator support | Massive complexity leap, no evidence of demand, single-cluster sufficient |
| OLM packaging | Declining adoption outside OpenShift, Helm is dominant distribution mechanism |
| Cross-namespace discovery | Security concern, violates least-privilege RBAC, discovery creates in same namespace |
| Auto-generated Python API docs from docstrings | Exposes internal domain code, facade guide covers public API surface |
| Helm umbrella chart combining server + operator | Couples lifecycle of independent components, separate charts are correct |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| DOC-01 | Phase 5 | Complete |
| DOC-02 | Phase 5 | Complete |
| DOC-03 | Phase 5 | Complete |
| DOC-04 | Phase 5 | Complete |
| DOC-05 | Phase 5 | Pending |
| DOC-06 | Phase 5 | Pending |
| DOC-07 | Phase 5 | Pending |
| DOC-08 | Phase 5 | Pending |
| K8S-01 | Phase 6 | Pending |
| K8S-02 | Phase 6 | Pending |
| K8S-03 | Phase 6 | Pending |
| K8S-04 | Phase 6 | Pending |
| K8S-05 | Phase 6 | Pending |
| K8S-06 | Phase 6 | Pending |
| K8S-07 | Phase 6 | Pending |
| HELM-01 | Phase 7 | Pending |
| HELM-02 | Phase 7 | Pending |
| HELM-03 | Phase 7 | Pending |

**Coverage:**

- v0.10 requirements: 18 total
- Mapped to phases: 18
- Unmapped: 0

---
*Requirements defined: 2026-02-28*
*Last updated: 2026-02-28 after 05-01 complete (DOC-01 through DOC-04 done)*
