# Roadmap: MCP Hangar

## Milestones

- ✅ **v0.9 Security Hardening** -- Phases 1-4 (shipped 2026-02-15)
- 🚧 **v0.10 Documentation & Kubernetes Maturity** -- Phases 5-7 (in progress)

## Phases

<details>
<summary>✅ v0.9 Security Hardening (Phases 1-4) -- SHIPPED 2026-02-15</summary>

- [x] Phase 1: Timing Attack Prevention (2/2 plans) -- completed 2026-02-15
- [x] Phase 2: Rate Limiter Hardening (2/2 plans) -- completed 2026-02-15
- [x] Phase 3: JWT Lifetime Enforcement (1/1 plan) -- completed 2026-02-15
- [x] Phase 4: API Key Rotation (2/2 plans) -- completed 2026-02-15

</details>

### 🚧 v0.10 Documentation & Kubernetes Maturity (In Progress)

- [x] **Phase 5: Documentation Content** - Create 4 missing documentation pages (Configuration Reference, MCP Tools Reference, Provider Groups Guide, Facade API Guide)
- [ ] **Phase 6: Kubernetes Controllers** - Implement MCPProviderGroup and MCPDiscoverySource controllers with integration tests
- [ ] **Phase 7: Helm Chart Maturity** - Synchronize both Helm charts to v0.10.0 with NOTES.txt and test templates

## Phase Details

### Phase 5: Documentation Content

**Goal**: Users can find comprehensive reference and guide documentation for configuration, tools, provider groups, and the facade API
**Depends on**: Nothing (documentation pages are independent of v0.10 controller/Helm work)
**Requirements**: DOC-01, DOC-02, DOC-03, DOC-04, DOC-05, DOC-06, DOC-07, DOC-08
**Success Criteria** (what must be TRUE):

  1. Configuration Reference page exists with full YAML schema (all keys, defaults, validation rules) and all environment variables with descriptions
  2. MCP Tools Reference page exists documenting all 22 tools with parameters, return formats, error codes, and side effects
  3. Provider Groups Guide exists covering all 5 load balancing strategies, health policies, circuit breaker, and tool access filtering with usage examples
  4. Facade API Guide exists documenting Hangar/SyncHangar public API with method signatures, HangarConfig builder, and framework integration patterns
  5. All 4 new pages are integrated into mkdocs.yml navigation and render correctly with `mkdocs build`
**Plans:** 2 plans

Plans:

- [x] 05-01-PLAN.md -- Reference pages: Configuration Reference (DOC-01, DOC-02) and MCP Tools Reference (DOC-03, DOC-04)
- [x] 05-02-PLAN.md -- Guide pages: Provider Groups Guide (DOC-05, DOC-06) and Facade API Guide (DOC-07, DOC-08) + mkdocs.yml nav integration

### Phase 6: Kubernetes Controllers

**Goal**: MCPProviderGroup and MCPDiscoverySource custom resources are reconciled by working controllers with full test coverage
**Depends on**: Nothing (Go operator work is independent of documentation)
**Requirements**: K8S-01, K8S-02, K8S-03, K8S-04, K8S-05, K8S-06, K8S-07
**Success Criteria** (what must be TRUE):

  1. MCPProviderGroup controller reconciles groups by selecting MCPProviders via label selector and aggregating their status (ready/degraded/dead counts)
  2. MCPProviderGroup controller evaluates health policies and reports conditions on the group status subresource
  3. MCPDiscoverySource controller discovers providers using all 4 modes (Namespace, ConfigMap, Annotations, ServiceDiscovery) and creates MCPProvider CRs with owner references
  4. MCPDiscoverySource controller supports both additive and authoritative sync modes (authoritative deletes only its own labeled resources)
  5. Both controllers pass envtest-based integration tests covering happy path and failure scenarios
**Plans**: TBD

### Phase 7: Helm Chart Maturity

**Goal**: Both Helm charts are version-synchronized and include post-install guidance and automated test validation
**Depends on**: Phase 6 (CRDs must be finalized before chart templates and tests reference them)
**Requirements**: HELM-01, HELM-02, HELM-03
**Success Criteria** (what must be TRUE):

  1. Both charts (mcp-hangar, mcp-hangar-operator) are at version 0.10.0 and pass `helm lint`
  2. Both charts include NOTES.txt with post-install instructions (endpoints, status commands, CRD upgrade guidance, docs links)
  3. Both charts include Helm test templates that validate installation (e.g., pod readiness, CRD existence)
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 5 -> 6 -> 7

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Timing Attack Prevention | v0.9 | 2/2 | Complete | 2026-02-15 |
| 2. Rate Limiter Hardening | v0.9 | 2/2 | Complete | 2026-02-15 |
| 3. JWT Lifetime Enforcement | v0.9 | 1/1 | Complete | 2026-02-15 |
| 4. API Key Rotation | v0.9 | 2/2 | Complete | 2026-02-15 |
| 5. Documentation Content | v0.10 | 2/2 | Complete | 2026-02-28 |
| 6. Kubernetes Controllers | v0.10 | 0/? | Not started | - |
| 7. Helm Chart Maturity | v0.10 | 0/? | Not started | - |

---
*Created: 2026-02-15*
*Last updated: 2026-02-28 after 05-02 plan complete (guide pages, Phase 5 complete)*
