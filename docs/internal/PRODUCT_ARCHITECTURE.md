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
  attributes for MCP server, tool, group, user, session, policy, and enforcement outcomes.
- Product investment stays focused on runtime governance, verification, identity-aware audit, and enforcement.

---

## 2. Licensing Model

### MIT License

All code in the repository is licensed under the MIT License. The `enterprise/` directory is a
code-organization concept for advanced features (RBAC, compliance, integrations), not a separate
license boundary.

### What goes where

| Feature                                            | Directory                                     | Rationale                                                       |
|----------------------------------------------------|-----------------------------------------------|-----------------------------------------------------------------|
| MCP Server lifecycle, state machine, circuit breaker | `src/`                                        | Core value, must be open for adoption                           |
| MCP Server groups, load balancing, failover          | `src/`                                        | Core value                                                      |
| Health checks, Prometheus metrics, OTEL export     | `src/`                                        | Observability foundation, enables partner integrations          |
| K8s operator, CRDs, Helm charts                    | `operator/`, `helm-charts/` (separate repos)  | Adoption requires open operator                                 |
| Capability declaration schema                      | `src/`                                        | Foundational for enforcement, must be standard                  |
| Network policy generation                          | `src/` + `operator/` (separate repo)          | Core enforcement, open for trust                                |
| Violation signals and enforcement events           | `src/`                                        | Core contract, partner backends consume these                   |
| CLI, hot-reload, batch invocations                 | `src/`                                        | Core DX                                                         |
| Basic audit logging (stdout/file)                  | `src/`                                        | Baseline visibility                                             |
| REST API, WebSocket infrastructure                 | `src/`                                        | API surface must be open                                        |
| RBAC, API key auth, JWT/OIDC                       | `enterprise/auth/`                            | Enterprise value, commercial differentiator                     |
| Tool Access Policies                               | `enterprise/auth/`                            | Governance feature, commercial differentiator                   |
| Event sourcing persistence (SQLite/Postgres)       | `enterprise/persistence/`                     | Enterprise durability, commercial differentiator                |
| Compliance export (CEF/LEEF/JSON-lines/syslog)     | `enterprise/compliance/`                      | Enterprise value                                                |
| Langfuse integration                               | `enterprise/integrations/`                    | Partner integration, commercial value                           |

### Architectural boundary

Enterprise features consume core interfaces. Core never imports from `enterprise/`. The boundary is a one-way
dependency:

```
enterprise/  ──depends-on──►  src/mcp_hangar/domain/contracts/
enterprise/  ──depends-on──►  src/mcp_hangar/application/ports/
enterprise/  ──never──►       imports from enterprise/ in core
```

This is enforced by:

1. CI check via `tools/check_enterprise_imports.py` — scans `src/` for static `enterprise` imports, fails on any new violation outside a tracked allowlist. Pre-commit hook (`enterprise-import-boundary`) runs the same check locally.
2. Core defines interfaces (ports/contracts). Enterprise provides implementations.
3. Bootstrap wiring in `server/bootstrap/` conditionally loads enterprise modules when available. Dynamic imports via `_import_attribute()` are the canonical pattern and are not flagged by the boundary check.

### Migration plan (historical — completed before v1.0.0)

The enterprise/ directory migration was completed before the v1.0.0 release. Pro/Enterprise features
were moved from `src/` to `enterprise/` and the import boundary is now CI-enforced.

---

## 3. Product Tiers

### Tier 0: Hangar Core (Open Source, MIT)

**Buyer:** Individual developer, small team, OSS community
**Entry:** `curl -sSL https://mcp-hangar.io/install.sh | bash`
**Value:** "See what your MCP servers do in 5 minutes."

**Includes:**

- MCP Server lifecycle management (state machine, health checks, circuit breaker)
- MCP Server groups (load balancing, failover)
- Docker and Kubernetes MCP server modes
- Hot-reload configuration
- Batch invocations with single-flight
- Prometheus metrics (full set)
- OpenTelemetry tracing export to partner backends (OpenLIT, Langfuse, Grafana stack, OTEL Collector)
- MCP-aware OTEL attribute taxonomy for governance telemetry (MCP server/tool/user/session/policy context)
- Capability declaration schema and network policy generation
- Violation signals and enforcement events
- Basic audit logging (stdout/file)
- Basic status CLI views
- CLI (`mcp-hangar init`, `mcp-hangar serve`, `mcp-hangar status`)
- MCP tools (hangar_tools, hangar_health, hangar_call, etc.)
- Helm chart for K8s deployment
- REST API and WebSocket infrastructure

### Tier 1: Hangar Pro

**Buyer:** Platform engineering team, 10-100 MCP servers
**Entry:** Self-hosted
**Price target:** $49-99/mo per cluster (or $499-999/yr)
**Value:** "Govern and secure your MCP servers with full visibility."

**Adds on top of Core:**

- RBAC (5 built-in roles) + API key authentication with rotation
- JWT/OIDC integration (Keycloak, Entra ID, Okta)
- Tool Access Policies (glob-pattern allow/deny, 3-level merge)
- Event sourcing persistence (SQLite, Postgres)
- Langfuse LLM observability integration
- Tool schema drift detection
- Behavioral reports (per-MCP server)
- Config export/backup

### Tier 2: Hangar Enterprise

**Buyer:** Organization with 100+ MCP servers, compliance requirements
**Entry:** Sales-led, consulting engagement
**Price target:** €2,000-5,000/mo or annual contract
**Value:** "Runtime security and compliance for MCP at scale."

**Adds on top of Pro:**

- Network behavioral profiling and deviation detection
- Caller identity propagation and identity-aware audit trail
- Call sequence pattern engine (semantic analysis)
- Pre-built detection rule packs
- Compliance export (CEF, LEEF, JSON-lines, syslog for SIEM)
- Cost attribution (FinOps per user/agent/MCP server)
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

**Decision:** Stdio MCP servers remain supported for development and simple setups only. New security and governance work
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

### Docker MCP server hardening priorities

Docker remains important, but primarily as the compatibility path below Kubernetes. Hardening work here should follow
patterns proven in the Kubernetes path rather than drive the roadmap.

| Item                  | Current state                   | Target state                                                  | Priority |
|-----------------------|---------------------------------|---------------------------------------------------------------|----------|
| Network isolation     | `none/bridge/host` option       | Default: dedicated bridge per MCP server, explicit egress rules | P0       |
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
| Stdio MCP server enhancements                                                                    | Working                  | **Freeze.** No security features for stdio.   | Cannot enforce network/filesystem policies on bare subprocess.                                       |
| Stdio governance/security investment                                                           | Supported path only      | **Stop expanding.** Maintenance only.         | Kubernetes operator, policies, and cluster-native controls are the only serious growth path.         |
| `mcp-hangar init` interactive flow                                                             | Polished (v0.6.6)        | **Maintain.**                                 | Good DX, complete for now.                                                                           |
| Redis cache backend                                                                            | Shipped                  | **Maintain.**                                 | Works, no further investment.                                                                        |
| Fuzz tests                                                                                     | Added in v0.12.0         | **Maintain.** Keep in CI, don't expand.       | Useful but not a differentiator.                                                                     |

---

## 6. Hardening Priorities — What Must Improve

### Critical (before any public positioning as "security layer")

| Area                              | Gap                                                                        | Action                                                                                                      | Target version | Status |
|-----------------------------------|----------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|----------------|--------|
| **Container network isolation**   | Docker MCP servers can talk to anything                                      | Default-deny egress, explicit allowlist                                                                     | v0.13.0        | |
| **Capability declaration schema** | No formal way to declare what a server needs                               | New `capabilities` config block                                                                             | v0.13.0        | |
| **K8s NetworkPolicy generation**  | Operator doesn't create NetworkPolicies                                    | Auto-generate from CRD capabilities field                                                                   | v0.13.0        | |
| **Licensing boundary**            | All code in MIT, no commercial protection                                  | Migrate Pro/Enterprise features to `enterprise/` directory                                                  | v0.13.0        | Completed |
| **Behavioral baseline storage**   | No behavioral profiling exists                                             | Network connection logging per container                                                                    | v0.14.0        | |
| **Test coverage on auth**         | Auth stack is comprehensive but test density unclear                       | Audit test coverage, target 90%+ on auth paths                                                              | v0.13.0        | |
| **Security scanning in CI**       | Not visible in changelog                                                   | Trivy/Grype on container images, Semgrep on source                                                          | v0.13.0        | |
| **Dependency audit**              | Not visible                                                                | `pip-audit`, `npm audit` in CI, SBOM generation                                                             | v0.13.0        | |
| **OTEL semantic conventions**     | Governance telemetry is useful but not yet formalized as a stable contract | Define MCP-aware OTEL conventions for MCP server/tool/user/session/policy/enforcement attributes              | v0.13.0        | **DONE** (v6.0 Phase 31) |
| **Trace context propagation**     | Cross-system traces depend on ad hoc correlation                           | Standardize agent -> Hangar -> MCP server trace propagation for audit and enforcement paths                   | v0.13.0        | **DONE** (v6.0 Phase 32) |
| **Operator enforcement loop**     | Operator reconciles state, but not full governance posture                 | Make operator the primary engine for capability enforcement, NetworkPolicy rollout, and violation signaling | v0.13.0        | |
| **Admission/policy hooks**        | K8s integration is not yet policy-driven enough                            | Validate and reject unsafe specs before runtime using admission and policy integrations                     | v0.13.0        | |
| **Import boundary enforcement**   | No CI rule prevents core from importing enterprise                         | Add CI check: `src/` must never import from `enterprise/`                                                   | v0.13.0        | **DONE** (TASK-P0-1) |

### Important (before first paying customer)

| Area                           | Gap                                                                                | Action                                                                                       | Target version | Status |
|--------------------------------|------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|----------------|--------|
| **Helm chart security**        | Basic                                                                              | Pod Security Standards, network policies, RBAC scoping                                       | v0.14.0        | |
| **Upgrade path**               | No migration guide between versions                                                | Documented upgrade procedure, DB migration tooling                                           | v0.14.0        | |
| **Performance benchmarks**     | Batch benchmark exists, nothing else                                               | Latency overhead of proxy path, max MCP servers per instance                                   | v0.14.0        | |
| **Error handling audit**       | Exception hygiene improved in v0.11.0                                              | Full audit of error surfaces exposed to users                                                | v0.14.0        | |
| **OTLP completeness**          | Traces exist, but partner story needs explicit completeness across telemetry types | Ensure security-relevant traces, metrics, and logs/audit signals are exportable through OTLP | v0.14.0        | **DONE** (v6.0 Phase 33) |
| **Integration recipes**        | OTEL partner story is implied, not operationalized                                 | Publish reference deployments for OpenLIT, OTEL Collector, Langfuse, and Grafana             | v0.14.0        | **DONE** (v6.0 Phase 34) |
| **License key infrastructure** | No mechanism to activate Pro/Enterprise                                            | Implement license key validation in bootstrap; enterprise modules load conditionally         | v0.14.0        | |

### Nice-to-have (H2 2026)

| Area                               | Gap             | Action                                             |
|------------------------------------|-----------------|----------------------------------------------------|
| Cosign/notation image verification | Not implemented | Add to container MCP server startup path             |
| Seccomp profiles                   | Not shipped     | Create and ship default MCP server seccomp profile |
| Multi-cluster federation           | Not implemented | Design doc first, implement when demand validated  |
| SCIM provisioning                  | Not implemented | Enterprise tier only                               |

---

## 7. Version Plan (historical)

> **Note:** This section preserves the original pre-1.0 plan for historical context.
> The actual release path was v0.12.0 → v1.0.0 (April 2026) → v1.1.0 (May 2026).
> The intermediate v0.13.0-v0.17.0 releases were never shipped. Current releases
> are tracked via release-please and the CHANGELOG.

| Version     | Target Date | Theme                                             | Outcome |
|-------------|-------------|---------------------------------------------------|---------|
| **v0.13.0** | 2026-04-15  | **Kubernetes Enforcement Foundation + Licensing** | Superseded by v1.0.0 |
| **v0.14.0** | 2026-05-15  | **Behavioral Profiling Alpha**                    | Not shipped |
| **v0.15.0** | 2026-06-15  | **Identity & Audit**                              | Not shipped |
| **v0.16.0** | 2026-07-15  | **Semantic Analysis Alpha**                       | Not shipped |
| **v1.0.0**  | 2026-09-29  | **Production Release**                            | Shipped April 2026 (ahead of schedule) |

### v1.0.0 criteria (historical — v1.0.0 shipped April 2026)

- [x] All P0 items from Phases 1-3 complete and tested
- [ ] K8s operator passes CIS benchmark (scoped)
- [ ] Docker MCP server default-deny egress enforced
- [x] Auth stack test coverage ≥ 90%
- [x] CI: Trivy, Semgrep, pip-audit, npm-audit green
- [x] Upgrade path documented from v0.12 → v1.0
- [ ] Performance: <5ms p99 overhead on proxy path
- [ ] At least 3 production deployments validated
- [x] Landing page, documentation site, blog post ready
- [x] MIT licensing for entire repository
- [x] Import boundary CI check green (no enterprise imports in core)

---

## 8. Repository Structure

### Current layout (post-v1.0)

```
mcp-hangar/
├── LICENSE                    # MIT — applies to the entire repository
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
├── enterprise/                # Advanced governance, enforcement, compliance
│   ├── auth/                  # RBAC, API key stores, JWT/OIDC, rate limiter, auth API
│   ├── approvals/             # Approval gate workflow
│   ├── compliance/            # SIEM export (CEF, LEEF, JSON-lines, syslog)
│   ├── persistence/           # SQLite/Postgres event stores, durable saga state
│   └── integrations/          # Langfuse adapter, future partner integrations
│
├── docs/                      # MkDocs documentation
│   └── internal/
│       └── PRODUCT_ARCHITECTURE.md  # This document
├── tests/                     # pytest test suite
└── scripts/                   # Install, build, CI, migration
```

### Import boundary rule

Enforced by `tools/check_enterprise_imports.py` (CI job `import-boundary` in `security.yml`, pre-commit hook `enterprise-import-boundary`). The script maintains an explicit allowlist of known tech-debt files with a removal target of 2026-Q3 (TASK-P0-2). New static `from enterprise` / `import enterprise` statements in `src/` fail CI immediately. Dynamic imports via `importlib` are the approved pattern and are not detected.

---

## 9. Competitive Intelligence — Key Gaps They Have

| Competitor               | What they lack (our opportunity)                                                                                                                                                                                                                                            |
|--------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Composio**             | No runtime behavior verification. Auth is their auth, not yours. No audit trail export. No K8s operator.                                                                                                                                                                    |
| **Smithery**             | "Config data is ephemeral" — zero runtime security. No governance. Community-submitted servers are unvetted.                                                                                                                                                                |
| **Glama**                | "Logging/traceability" is a bullet point, not a product. No behavioral profiling. No capability enforcement.                                                                                                                                                                |
| **OpenLIT**              | Excellent AI observability and MCP telemetry partner. Missing: MCP server lifecycle control, runtime enforcement, failover/group management, capability verification, and MCP-native governance semantics. We should integrate through OTEL, not imitate the product surface. |
| **MCP Gateway Registry** | Closest to us. Has audit logs, RBAC, OTLP telemetry. Missing: behavioral profiling, capability verification, semantic analysis, identity propagation. Their OTLP is generic; ours is MCP-aware.                                                                             |
| **CData Connect AI**     | Enterprise wrapper. Governance = their dashboard. No open source. No protocol-level understanding.                                                                                                                                                                          |

---

## 10. Decision Log

| Date       | Decision                                                                                                         | Rationale                                                                                                                                                                                                                                   |
|------------|------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 2026-03-24 | **~~BSL 1.1 for enterprise features, MIT for core.~~ Superseded: all code relicensed to MIT in v1.3.0.**         | Original: commercial protection. Dropped: complexity outweighed commercial returns. See epic #198.                                                                                                                                          |
| 2026-03-24 | **Enterprise/ directory migration before v0.13.0.**                                                              | Licensing boundary must be established before enterprise features are developed further. Retrofitting is harder than doing it right from the start.                                                                                         |
| 2026-03-24 | **~~CLA required for enterprise/ contributions.~~ Dropped in v1.3.0.**                                          | No longer needed under single-MIT. Contributions flow inbound=outbound MIT.                                                                                                                                                                |
| 2026-03-23 | Docker/K8s first. Stdio is second-class for security features.                                                   | Runtime security requires container isolation. Period.                                                                                                                                                                                      |
| 2026-03-23 | Freeze Catalog API development.                                                                                  | Not our market. Discovery is Smithery/Registry.                                                                                                                                                                                             |
| 2026-03-23 | Integrate with OpenTelemetry-native observability tools (for example OpenLIT) instead of trying to replace them. | Win on governance and enforcement, not on copying generic AI observability platforms.                                                                                                                                                       |
| 2026-03-23 | Treat OTEL as a first-class product contract for partner integrations.                                           | Strong OTEL semantics let Hangar project governance telemetry into OpenLIT, Langfuse, Grafana, and other backends without product drift.                                                                                                    |
| 2026-03-23 | Kubernetes is the primary growth path; Docker follows, stdio is maintenance only.                                | Operator-driven governance, NetworkPolicy, admission, and violation handling are where defensible product value lives.                                                                                                                      |
| 2026-03-23 | Three-tier product model (Core/Pro/Enterprise).                                                                  | Need open source adoption funnel AND revenue path.                                                                                                                                                                                          |
| 2026-03-23 | v1.0.0 target: September 2026.                                                                                   | 6-month window before major vendors enter MCP observability.                                                                                                                                                                                |
| 2026-03-23 | Position as "runtime security," not "control plane."                                                             | "Control plane" is generic. "Runtime security and governance" is specific and defensible.                                                                                                                                                   |
