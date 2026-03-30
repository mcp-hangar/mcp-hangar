# MCP Hangar — Product Architecture & Hardening Plan

> **Classification:** Internal — do not publish
> **Author:** Marcin
> **Date:** 2026-03-24
> **Purpose:** Define product tiers, hardening priorities, cut list, and deployment focus

---

## 1. Product Identity

### One-liner

**MCP Hangar is the runtime security and governance layer for MCP servers in production.**

### Positioning matrix

| Player               | What they do                                                      | Where Hangar fits                                                                                                             |
|----------------------|-------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| Composio             | Managed integrations (500+ connectors, auth)                      | They're plumbing. We govern the plumbing.                                                                                     |
| Smithery             | Server discovery & hosted deployment                              | They're a registry. We verify what's in the registry.                                                                         |
| Glama                | MCP hosting platform & API gateway                                | They run servers. We watch what servers do.                                                                                   |
| OpenLIT              | AI observability, evaluations, prompts, telemetry UX              | Strong partner/integration on visibility. Not a replacement for runtime governance, lifecycle control, or policy enforcement. |
| MCP Gateway Registry | Enterprise gateway with OAuth & RBAC                              | Closest competitor. Missing behavioral profiling, runtime verification, semantic analysis.                                    |
| Datadog/Grafana      | General observability                                             | Generic. No MCP protocol awareness, no capability enforcement, no tool-level governance.                                      |
| **MCP Hangar**       | **Runtime security, behavioral governance, lifecycle management** | **The layer between "deployed" and "trusted."**                                                                               |

### Tagline candidates (pick one, kill the rest)

1. "Know what your agents are doing before they do it."
2. "Your MCP servers don't crash gracefully. Hangar knows before they crash at all."
3. "MCP servers are black boxes. Hangar opens them."
4. "Deploy MCP servers. Govern MCP servers. Trust MCP servers."

### Integration stance

- OpenTelemetry-compatible tools such as `OpenLIT`, `Langfuse`, Grafana, and other OTLP backends are **extensions to the
  visibility layer** around Hangar.
- Hangar exports telemetry to them; Hangar does **not** try to become a generic AI observability platform.
- OpenTelemetry is the **interoperability contract**: Hangar should emit MCP-aware governance telemetry with stable
  attributes for provider, tool, group, user, session, policy, and enforcement outcomes.
- Product investment stays focused on runtime governance, verification, identity-aware audit, and enforcement.

---

## 2. Licensing Model

### Dual-license: MIT core + BSL 1.1 enterprise

| Component           | License | Scope                                                                                                                      |
|---------------------|---------|----------------------------------------------------------------------------------------------------------------------------|
| Core control plane  | MIT     | `src/mcp_hangar/` |
| Enterprise features | BSL 1.1 | `enterprise/` — behavioral profiling, semantic analysis, identity propagation, compliance export |

### BSL parameters

- **Licensor:** Marcin (MCP Hangar project)
- **Licensed Work:** Each release of code under `enterprise/`
- **Change Date:** 3 years from release date of each version
- **Change License:** MIT
- **Additional Use Grant:** Evaluation, development, testing, and non-production use are permitted without a commercial
  license. Production use for internal tooling of organizations with fewer than 5 MCP servers is permitted (community
  use exception).

### Why BSL over alternatives

| Alternative                            | Rejected because                                                                                                                                                          |
|----------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Full MIT everywhere                    | No commercial protection. Anyone can host "Hangar Enterprise" and sell your work. One-person operation cannot compete on price with a cloud provider reselling your code. |
| Dual repo (private enterprise repo)    | Two repos to maintain solo. Merge conflicts between core and enterprise. Contributors can't read enterprise code. Enterprise buyers can't audit before purchase.          |
| Feature flags without legal protection | Trivially bypassed. No legal recourse. Invites the Hetzner-hosted competitor problem.                                                                                     |
| AGPL                                   | Scares away enterprise buyers. Many companies have blanket AGPL prohibition policies.                                                                                     |
| SSPL                                   | Even more restrictive than AGPL. MongoDB backlash. Not accepted by most enterprises.                                                                                      |

### What goes where

| Feature                                            | License | Directory                                     | Rationale                                                       |
|----------------------------------------------------|---------|-----------------------------------------------|-----------------------------------------------------------------|
| Provider lifecycle, state machine, circuit breaker | MIT     | `src/`                                        | Core value, must be open for adoption                           |
| Provider groups, load balancing, failover          | MIT     | `src/`                                        | Core value                                                      |
| Health checks, Prometheus metrics, OTEL export     | MIT     | `src/`                                        | Observability foundation, enables partner integrations          |
| K8s operator, CRDs, Helm charts                    | MIT     | `operator/`, `helm-charts/` (separate repos)  | Adoption requires open operator                                 |
| Capability declaration schema                      | MIT     | `src/`                                        | Foundational for enforcement, must be standard                  |
| Network policy generation                          | MIT     | `src/` + `operator/` (separate repo)          | Core enforcement, open for trust                                |
| Violation signals and enforcement events           | MIT     | `src/`                                        | Core contract, partner backends consume these                   |
| CLI, hot-reload, batch invocations                 | MIT     | `src/`                                        | Core DX                                                         |
| Basic audit logging (stdout/file)                  | MIT     | `src/`                                        | Baseline visibility                                             |
| REST API, WebSocket infrastructure                 | MIT     | `src/`                                        | API surface must be open                                        |
| RBAC, API key auth, JWT/OIDC                       | **BSL** | `enterprise/auth/`                            | Enterprise value, commercial differentiator                     |
| Tool Access Policies                               | **BSL** | `enterprise/policies/`                        | Governance feature, commercial differentiator                   |
| Event sourcing persistence (SQLite/Postgres)       | **BSL** | `enterprise/persistence/`                     | Enterprise durability, commercial differentiator                |
| Behavioral profiling and deviation detection       | **BSL** | `enterprise/behavioral/`                      | Core thesis of Enterprise tier                                  |
| Caller identity propagation                        | **BSL** | `enterprise/identity/`                        | Enterprise value                                                |
| Identity-aware audit trail                         | **BSL** | `enterprise/identity/`                        | Enterprise value                                                |
| Compliance export (CEF/LEEF/JSON-lines)            | **BSL** | `enterprise/compliance/`                      | Enterprise value                                                |
| Cost attribution / FinOps                          | **BSL** | `enterprise/finops/`                          | Enterprise value                                                |
| Call sequence pattern engine                       | **BSL** | `enterprise/semantic/`                        | Core thesis of Enterprise tier                                  |
| Detection rule packs                               | **BSL** | `enterprise/semantic/rules/`                  | Commercial IP                                                   |
| Custom rule DSL                                    | **BSL** | `enterprise/semantic/`                        | Enterprise value                                                |
| Agent behavior scoring                             | **BSL** | `enterprise/semantic/`                        | Enterprise value                                                |
| Langfuse integration                               | **BSL** | `enterprise/integrations/`                    | Partner integration, commercial value                           |

### Architectural boundary

Enterprise features consume core interfaces. Core never imports from `enterprise/`. The boundary is a one-way
dependency:

```
enterprise/  ──depends-on──►  src/mcp_hangar/domain/contracts/
enterprise/  ──depends-on──►  src/mcp_hangar/application/ports/
enterprise/  ──never──►       imports from enterprise/ in core
```

This is enforced by:

1. Import linting rule in CI: no `enterprise.*` imports in `src/`
2. Core defines interfaces (ports/contracts). Enterprise provides implementations.
3. Bootstrap wiring in `server/bootstrap/` conditionally loads enterprise modules when license key is present.

### Migration plan (v0.12.0 → v0.13.0)

Existing Pro/Enterprise features currently live in `src/`. They must move to `enterprise/` before v0.13.0 release.

| Current location                                                             | Target location                                                     | Feature                                              |
|------------------------------------------------------------------------------|---------------------------------------------------------------------|------------------------------------------------------|
| `src/mcp_hangar/infrastructure/auth/`                                        | `enterprise/auth/`                                                  | API key stores, JWT/OIDC, RBAC, rate limiter         |
| `src/mcp_hangar/domain/security/roles.py`                                    | `enterprise/auth/roles.py`                                          | Role definitions (contracts/interfaces stay in core) |
| `src/mcp_hangar/server/api/auth/`                                            | `enterprise/auth/api/`                                              | Auth REST endpoints                                  |
| `src/mcp_hangar/server/auth_bootstrap.py`                                    | `enterprise/auth/bootstrap.py`                                      | Auth DI wiring                                       |
| `src/mcp_hangar/domain/value_objects/tool_access_policy.py`                  | Keep interface in core, move enforcement to `enterprise/policies/`  | Policy enforcement                                   |
| `src/mcp_hangar/infrastructure/persistence/event_store.py` (SQLite/Postgres) | `enterprise/persistence/`                                           | Durable event stores (in-memory stays in core)       |
| `src/mcp_hangar/infrastructure/observability/langfuse_adapter.py`            | `enterprise/integrations/langfuse.py`                               | Langfuse integration                                 |

**Migration steps:**

1. Extract interfaces/contracts for every feature being moved. Ensure they exist in `src/mcp_hangar/domain/contracts/`
   or `src/mcp_hangar/application/ports/`.
2. Move implementation files to `enterprise/` directory.
3. Update bootstrap to conditionally load enterprise modules.
4. Add CI rule: `grep -r "from enterprise" src/` must return empty.
5. Add `enterprise/LICENSE.BSL` file.
6. Update root `LICENSE` to clarify scope (MIT for everything outside `enterprise/`).
7. Tag v0.13.0 with dual-license in place.

### CLA requirement

Contributors to `enterprise/` must sign a Contributor License Agreement granting the project maintainer (Marcin) the
right to relicense their contributions. This is necessary because BSL → MIT conversion requires licensing authority over
all contributed code.

Core (MIT) contributions do not require a CLA.

---

## 3. Product Tiers

### Tier 0: Hangar Core (Open Source, MIT)

**Buyer:** Individual developer, small team, OSS community
**Entry:** `curl -sSL https://mcp-hangar.io/install.sh | bash`
**Value:** "See what your MCP servers do in 5 minutes."

**Includes:**

- Provider lifecycle management (state machine, health checks, circuit breaker)
- Provider groups (load balancing, failover)
- Docker and Kubernetes provider modes
- Hot-reload configuration
- Batch invocations with single-flight
- Prometheus metrics (full set)
- OpenTelemetry tracing export to partner backends (OpenLIT, Langfuse, Grafana stack, OTEL Collector)
- MCP-aware OTEL attribute taxonomy for governance telemetry (provider/tool/user/session/policy context)
- Capability declaration schema and network policy generation
- Violation signals and enforcement events
- Basic audit logging (stdout/file)
- Basic status CLI views
- CLI (`hangar init`, `hangar serve`, `hangar status`)
- MCP tools (hangar_tools, hangar_health, hangar_invoke, etc.)
- Helm chart for K8s deployment
- REST API and WebSocket infrastructure

### Tier 1: Hangar Pro (BSL 1.1, commercial license)

**Buyer:** Platform engineering team, 10-100 MCP servers
**Entry:** Self-hosted, license key activation
**Price target:** $49-99/mo per cluster (or $499-999/yr)
**Value:** "Govern and secure your MCP servers with full visibility."

**Adds on top of Core:**

- RBAC (5 built-in roles) + API key authentication with rotation
- JWT/OIDC integration (Keycloak, Entra ID, Okta)
- Tool Access Policies (glob-pattern allow/deny, 3-level merge)
- Event sourcing persistence (SQLite, Postgres)
- Langfuse LLM observability integration
- Tool schema drift detection
- Behavioral reports (per-provider)
- Config export/backup

### Tier 2: Hangar Enterprise (BSL 1.1, custom commercial terms)

**Buyer:** Organization with 100+ MCP servers, compliance requirements
**Entry:** Sales-led, consulting engagement
**Price target:** €2,000-5,000/mo or annual contract
**Value:** "Runtime security and compliance for MCP at scale."

**Adds on top of Pro:**

- Network behavioral profiling and deviation detection
- Caller identity propagation and identity-aware audit trail
- Call sequence pattern engine (semantic analysis)
- Pre-built detection rule packs
- Compliance export (CEF, LEEF, JSON-lines for SIEM)
- Cost attribution (FinOps per user/agent/provider)
- Multi-cluster federation (H2 2026)
- SSO / SCIM user provisioning
- Priority support + SLA

### Tier 3: Hangar Advisory (Consulting)

**Buyer:** Any organization deploying MCP servers
**Entry:** Direct outreach, inbound from content/newsletter
**Price:** €800-1,200/day

**Offerings:**

| Engagement                | Duration  | Price           | Deliverable                                               |
|---------------------------|-----------|-----------------|-----------------------------------------------------------|
| MCP Operations Assessment | 2-3 days  | €2,400-3,600    | Audit report, Maturity Scorecard, recommendations         |
| MCP Security Assessment   | 3-5 days  | €3,600-6,000    | Behavioral profile, risk matrix, network policy templates |
| Hangar Implementation     | 2-4 weeks | €8,000-16,000   | Full deployment, dashboards, runbooks, team training      |
| Advisory Retainer         | Monthly   | €2,000-4,000/mo | Ongoing review, tuning, incident support                  |

---

## 4. Deployment Focus: Kubernetes First, Docker Compatible

### Why Kubernetes-first is non-negotiable

The runtime security thesis requires:

| Capability                 | Container (Docker/K8s)            | Stdio (subprocess)                  |
|----------------------------|-----------------------------------|-------------------------------------|
| Network policy enforcement | ✅ NetworkPolicy, iptables         | ❌ Shares host network               |
| Outbound traffic profiling | ✅ Container network namespace     | ❌ Mixed with host traffic           |
| Filesystem isolation       | ✅ Read-only root, explicit mounts | ⚠️ Process-level only               |
| Resource limits            | ✅ cgroups                         | ⚠️ ulimits (weaker)                 |
| Capability dropping        | ✅ seccomp, AppArmor               | ❌ Not applicable                    |
| Image provenance           | ✅ cosign/notation verification    | ❌ No equivalent                     |
| Behavioral baseline        | ✅ Isolated network namespace      | ❌ Cannot distinguish server traffic |

**Decision:** Stdio providers remain supported for development and simple setups only. New security and governance work
targets Kubernetes first, then Docker where practical. Documentation and product direction lead with operator-driven
Kubernetes deployment; Docker remains the compatibility and local-development path.

### K8s Operator hardening priorities

| Item                         | Current state              | Target state                                                                      | Priority |
|------------------------------|----------------------------|-----------------------------------------------------------------------------------|----------|
| CRD validation               | Basic                      | CEL validation rules, webhook admission                                           | P0       |
| NetworkPolicy generation     | Not implemented            | Auto-generated from capability declaration                                        | P0       |
| Violation signaling          | Not implemented            | First-class `violation` and `enforcement` events surfaced from operator decisions | P0       |
| Policy ecosystem integration | Minimal                    | Integrate with admission/policy tooling and operator-managed governance flows     | P0       |
| Pod Security Standards       | Partial (security context) | Enforce `restricted` PSS by default                                               | P0       |
| RBAC scoping                 | Cluster-wide               | Namespace-scoped with aggregated ClusterRoles                                     | P1       |
| Operator HA                  | Leader election exists     | Anti-affinity, PDB, multi-replica                                                 | P1       |
| Helm chart hardening         | Basic                      | CIS benchmark aligned, OPA/Kyverno policies shipped                               | P1       |
| Upgrade strategy             | Not defined                | CRD versioning, conversion webhooks, migration guide                              | P2       |

### Docker provider hardening priorities

Docker remains important, but primarily as the compatibility path below Kubernetes. Hardening work here should follow
patterns proven in the Kubernetes path rather than drive the roadmap.

| Item                  | Current state                   | Target state                                                  | Priority |
|-----------------------|---------------------------------|---------------------------------------------------------------|----------|
| Network isolation     | `none/bridge/host` option       | Default: dedicated bridge per provider, explicit egress rules | P0       |
| Default security opts | Dropped caps, no-new-privileges | + seccomp profile, read-only root, tmpfs for /tmp             | P0       |
| Egress allowlist      | Not implemented                 | Config-driven outbound destination allowlist, deny all else   | P0       |
| DNS monitoring        | Not implemented                 | Capture DNS queries per container for behavioral baseline     | P1       |
| Volume mount audit    | Blocked sensitive paths         | Audit log of all file reads/writes in mounted volumes         | P2       |

---

## 5. Cut List — What to Deprioritize

These features exist in the codebase but are not on the critical path. They should not receive development time until
Phases 1-2 are complete.

| Feature                                                                                        | Current state            | Action                                        | Reason                                                                                               |
|------------------------------------------------------------------------------------------------|--------------------------|-----------------------------------------------|------------------------------------------------------------------------------------------------------|
| Catalog API                                                                                    | Experimental (v0.12.0)   | **Freeze.** No new work.                      | Discovery/catalog is Smithery/Registry territory. Not our game.                                      |
| D3 topology visualization                                                                      | Shipped in dashboard     | **Freeze.** Maintain, don't enhance.          | Cool demo, zero business value until there are paying users.                                         |
| Config export UI with diff viewer                                                              | Shipped                  | **Freeze.**                                   | Nice-to-have. Not on the buyer's decision matrix.                                                    |
| Generic observability platform features (prompt hub, playground, broad eval suite, secrets UX) | Adjacent market only     | **Do not build. Integrate instead.**          | OpenLIT and similar platforms already serve this layer. Our lane is runtime security and governance. |
| Response truncation / continuation cache                                                       | Shipped (v0.6.3)         | **Maintain.** Bug fixes only.                 | Solid feature, complete, no further investment needed.                                               |
| Saga compensation                                                                              | Shipped with persistence | **Maintain.**                                 | Infrastructure piece, done.                                                                          |
| Binary installer                                                                               | Shipped in v0.6.0        | **Deprioritize.** Docker/K8s path is primary. | Binary installs don't benefit from container security.                                               |
| Stdio provider enhancements                                                                    | Working                  | **Freeze.** No security features for stdio.   | Cannot enforce network/filesystem policies on bare subprocess.                                       |
| Stdio governance/security investment                                                           | Supported path only      | **Stop expanding.** Maintenance only.         | Kubernetes operator, policies, and cluster-native controls are the only serious growth path.         |
| `mcp-hangar init` interactive flow                                                             | Polished (v0.6.6)        | **Maintain.**                                 | Good DX, complete for now.                                                                           |
| Redis cache backend                                                                            | Shipped                  | **Maintain.**                                 | Works, no further investment.                                                                        |
| Fuzz tests                                                                                     | Added in v0.12.0         | **Maintain.** Keep in CI, don't expand.       | Useful but not a differentiator.                                                                     |

---

## 6. Hardening Priorities — What Must Improve

### Critical (before any public positioning as "security layer")

| Area                              | Gap                                                                        | Action                                                                                                      | Target version | Status |
|-----------------------------------|----------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|----------------|--------|
| **Container network isolation**   | Docker providers can talk to anything                                      | Default-deny egress, explicit allowlist                                                                     | v0.13.0        | |
| **Capability declaration schema** | No formal way to declare what a server needs                               | New `capabilities` config block                                                                             | v0.13.0        | |
| **K8s NetworkPolicy generation**  | Operator doesn't create NetworkPolicies                                    | Auto-generate from CRD capabilities field                                                                   | v0.13.0        | |
| **Licensing boundary**            | All code in MIT, no commercial protection                                  | Migrate Pro/Enterprise features to `enterprise/` under BSL 1.1                                              | v0.13.0        | In progress (v7.0 Phase 36) |
| **Behavioral baseline storage**   | No behavioral profiling exists                                             | Network connection logging per container                                                                    | v0.14.0        | |
| **Test coverage on auth**         | Auth stack is comprehensive but test density unclear                       | Audit test coverage, target 90%+ on auth paths                                                              | v0.13.0        | |
| **Security scanning in CI**       | Not visible in changelog                                                   | Trivy/Grype on container images, Semgrep on source                                                          | v0.13.0        | |
| **Dependency audit**              | Not visible                                                                | `pip-audit`, `npm audit` in CI, SBOM generation                                                             | v0.13.0        | |
| **OTEL semantic conventions**     | Governance telemetry is useful but not yet formalized as a stable contract | Define MCP-aware OTEL conventions for provider/tool/user/session/policy/enforcement attributes              | v0.13.0        | **DONE** (v6.0 Phase 31) |
| **Trace context propagation**     | Cross-system traces depend on ad hoc correlation                           | Standardize agent -> Hangar -> provider trace propagation for audit and enforcement paths                   | v0.13.0        | **DONE** (v6.0 Phase 32) |
| **Operator enforcement loop**     | Operator reconciles state, but not full governance posture                 | Make operator the primary engine for capability enforcement, NetworkPolicy rollout, and violation signaling | v0.13.0        | |
| **Admission/policy hooks**        | K8s integration is not yet policy-driven enough                            | Validate and reject unsafe specs before runtime using admission and policy integrations                     | v0.13.0        | |
| **Import boundary enforcement**   | No CI rule prevents core from importing enterprise                         | Add CI check: `src/` must never import from `enterprise/`                                                   | v0.13.0        | |

### Important (before first paying customer)

| Area                           | Gap                                                                                | Action                                                                                       | Target version | Status |
|--------------------------------|------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|----------------|--------|
| **Helm chart security**        | Basic                                                                              | Pod Security Standards, network policies, RBAC scoping                                       | v0.14.0        | |
| **Upgrade path**               | No migration guide between versions                                                | Documented upgrade procedure, DB migration tooling                                           | v0.14.0        | |
| **Performance benchmarks**     | Batch benchmark exists, nothing else                                               | Latency overhead of proxy path, max providers per instance                                   | v0.14.0        | |
| **Error handling audit**       | Exception hygiene improved in v0.11.0                                              | Full audit of error surfaces exposed to users                                                | v0.14.0        | |
| **OTLP completeness**          | Traces exist, but partner story needs explicit completeness across telemetry types | Ensure security-relevant traces, metrics, and logs/audit signals are exportable through OTLP | v0.14.0        | **DONE** (v6.0 Phase 33) |
| **Integration recipes**        | OTEL partner story is implied, not operationalized                                 | Publish reference deployments for OpenLIT, OTEL Collector, Langfuse, and Grafana             | v0.14.0        | **DONE** (v6.0 Phase 34) |
| **License key infrastructure** | No mechanism to activate Pro/Enterprise                                            | Implement license key validation in bootstrap; enterprise modules load conditionally         | v0.14.0        | |

### Nice-to-have (H2 2026)

| Area                               | Gap             | Action                                             |
|------------------------------------|-----------------|----------------------------------------------------|
| Cosign/notation image verification | Not implemented | Add to container provider startup path             |
| Seccomp profiles                   | Not shipped     | Create and ship default MCP server seccomp profile |
| Multi-cluster federation           | Not implemented | Design doc first, implement when demand validated  |
| SCIM provisioning                  | Not implemented | Enterprise tier only                               |

---

## 7. Version Plan

| Version     | Target Date | Theme                                             | Key Deliverables                                                                                                                                                                                                                                                                                     |
|-------------|-------------|---------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **v0.13.0** | 2026-04-15  | **Kubernetes Enforcement Foundation + Licensing** | Capability declaration schema, operator enforcement loop, K8s NetworkPolicy generation, admission/policy hooks, CI security scanning, auth test hardening, OTEL semantic conventions, trace propagation, **BSL licensing in place, enterprise/ directory created, Pro/Enterprise features migrated** |
| **v0.14.0** | 2026-05-15  | **Behavioral Profiling Alpha**                    | Network connection logging per container, behavioral baseline storage, deviation alerting, dashboard auth enforcement, OTLP completeness, partner integration recipes, violation/enforcement signal modeling, **license key infrastructure**                                                         |
| **v0.15.0** | 2026-06-15  | **Identity & Audit**                              | Caller identity propagation, identity-aware audit trail, compliance export (CEF/JSON-lines), cost attribution MVP                                                                                                                                                                                    |
| **v0.16.0** | 2026-07-15  | **Semantic Analysis Alpha**                       | Call sequence pattern engine, pre-built detection rules (exfiltration, escalation, recon), dashboard integration                                                                                                                                                                                     |
| **v1.0.0**  | 2026-09-01  | **Production Release**                            | Stability, documentation, upgrade tooling, performance benchmarks, public launch                                                                                                                                                                                                                     |

### v1.0.0 criteria

- [ ] All P0 items from Phases 1-3 complete and tested
- [ ] K8s operator passes CIS benchmark (scoped)
- [ ] Docker provider default-deny egress enforced
- [ ] Auth stack test coverage ≥ 90%
- [ ] CI: Trivy, Semgrep, pip-audit, npm-audit green
- [ ] Upgrade path documented from v0.12 → v1.0
- [ ] Performance: <5ms p99 overhead on proxy path
- [ ] At least 3 production deployments validated
- [ ] Landing page, documentation site, blog post ready
- [ ] BSL licensing fully operational with license key validation
- [ ] Import boundary CI check green (no enterprise imports in core)

---

## 8. Repository Structure (Current → Target)

### Target layout after v0.13.0 migration

```
mcp-hangar/
├── LICENSE                    # MIT — applies to everything outside enterprise/
├── ROADMAP.md                 # Public roadmap
├── CLA.md                     # Contributor License Agreement for enterprise/ contributions
│
├── src/mcp_hangar/            # MIT — core control plane
│   ├── domain/                # DDD aggregates, value objects, events, contracts
│   │   ├── contracts/         # Interfaces consumed by enterprise/ (one-way dependency)
│   │   └── ...
│   ├── application/           # CQRS commands, queries, handlers, ports
│   │   ├── ports/             # Port interfaces consumed by enterprise/ (one-way dependency)
│   │   └── ...
│   ├── infrastructure/        # Core adapters (in-memory stores, Docker, K8s, OTEL)
│   └── server/                # MCP server, REST API, WebSocket, CLI, bootstrap
│       └── bootstrap/         # Conditionally loads enterprise/ modules when license present
│
├── enterprise/                # BSL 1.1 — advanced governance, enforcement, compliance
│   ├── LICENSE.BSL            # Business Source License 1.1
│   ├── auth/                  # RBAC, API key stores, JWT/OIDC, rate limiter, auth API
│   ├── policies/              # Tool Access Policy enforcement
│   ├── persistence/           # SQLite/Postgres event stores, durable saga state
│   ├── behavioral/            # Network profiling, baseline, deviation detection
│   ├── identity/              # Caller identity propagation, identity-aware audit
│   ├── compliance/            # SIEM export (CEF, LEEF, JSON-lines)
│   ├── finops/                # Cost attribution, token tracking
│   ├── semantic/              # Pattern engine, detection rules, rule DSL, scoring
│   │   └── rules/             # Pre-built detection rule packs
│   └── integrations/          # Langfuse adapter, future partner integrations
│
├── packages/
│   ├── operator/              # MIT — K8s operator (Go)
│   ├── helm-charts/           # MIT — Helm charts
│   └── ui/                    # Basic status views (MIT), full dashboard (enterprise/)
│
├── security/                  # Seccomp profiles, AppArmor, NetworkPolicy templates, detection rules
├── benchmarks/                # Performance benchmark suite
├── docker/                    # Provider container images
├── docs/                      # MkDocs documentation
│   └── internal/
│       └── PRODUCT_ARCHITECTURE.md  # This document
├── examples/                  # Quick starts, configs
├── monitoring/                # Grafana dashboards, Prometheus alerts
└── scripts/                   # Install, build, CI, migration
```

### Import boundary rule

```
# CI check (must pass on every PR)
# Core must never depend on enterprise features
if grep -rn "from enterprise" src/; then
  echo "FAIL: core imports enterprise module"
  exit 1
fi
```

---

## 9. Competitive Intelligence — Key Gaps They Have

| Competitor               | What they lack (our opportunity)                                                                                                                                                                                                                                            |
|--------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Composio**             | No runtime behavior verification. Auth is their auth, not yours. No audit trail export. No K8s operator.                                                                                                                                                                    |
| **Smithery**             | "Config data is ephemeral" — zero runtime security. No governance. Community-submitted servers are unvetted.                                                                                                                                                                |
| **Glama**                | "Logging/traceability" is a bullet point, not a product. No behavioral profiling. No capability enforcement.                                                                                                                                                                |
| **OpenLIT**              | Excellent AI observability and MCP telemetry partner. Missing: provider lifecycle control, runtime enforcement, failover/group management, capability verification, and MCP-native governance semantics. We should integrate through OTEL, not imitate the product surface. |
| **MCP Gateway Registry** | Closest to us. Has audit logs, RBAC, OTLP telemetry. Missing: behavioral profiling, capability verification, semantic analysis, identity propagation. Their OTLP is generic; ours is MCP-aware.                                                                             |
| **CData Connect AI**     | Enterprise wrapper. Governance = their dashboard. No open source. No protocol-level understanding.                                                                                                                                                                          |

---

## 10. Decision Log

| Date       | Decision                                                                                                         | Rationale                                                                                                                                                                                                                                   |
|------------|------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 2026-03-24 | **BSL 1.1 for enterprise features, MIT for core.**                                                               | One repo, full source transparency, legal protection against commercial free-riding. BSL→MIT conversion after 3 years per release keeps the project honest. Alternatives (dual repo, AGPL, feature flags) rejected — see licensing section. |
| 2026-03-24 | **Enterprise/ directory migration before v0.13.0.**                                                              | Licensing boundary must be established before enterprise features are developed further. Retrofitting is harder than doing it right from the start.                                                                                         |
| 2026-03-24 | **CLA required for enterprise/ contributions.**                                                                  | BSL→MIT conversion requires licensing authority over all contributed code in the enterprise directory.                                                                                                                                      |
| 2026-03-23 | Docker/K8s first. Stdio is second-class for security features.                                                   | Runtime security requires container isolation. Period.                                                                                                                                                                                      |
| 2026-03-23 | Freeze Catalog API development.                                                                                  | Not our market. Discovery is Smithery/Registry.                                                                                                                                                                                             |
| 2026-03-23 | Integrate with OpenTelemetry-native observability tools (for example OpenLIT) instead of trying to replace them. | Win on governance and enforcement, not on copying generic AI observability platforms.                                                                                                                                                       |
| 2026-03-23 | Treat OTEL as a first-class product contract for partner integrations.                                           | Strong OTEL semantics let Hangar project governance telemetry into OpenLIT, Langfuse, Grafana, and other backends without product drift.                                                                                                    |
| 2026-03-23 | Kubernetes is the primary growth path; Docker follows, stdio is maintenance only.                                | Operator-driven governance, NetworkPolicy, admission, and violation handling are where defensible product value lives.                                                                                                                      |
| 2026-03-23 | Three-tier product model (Core/Pro/Enterprise).                                                                  | Need open source adoption funnel AND revenue path.                                                                                                                                                                                          |
| 2026-03-23 | v1.0.0 target: September 2026.                                                                                   | 6-month window before major vendors enter MCP observability.                                                                                                                                                                                |
| 2026-03-23 | Position as "runtime security," not "control plane."                                                             | "Control plane" is generic. "Runtime security and governance" is specific and defensible.                                                                                                                                                   |
