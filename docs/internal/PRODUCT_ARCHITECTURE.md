# MCP Hangar — Product Architecture & Hardening Plan

> **Classification:** Internal — do not publish
> **Author:** Marcin
> **Date:** 2026-03-24 · **Last reconciled against the source:** 2026-08-11 (core 2.5.2, operator v0.15.1)
> **Purpose:** Capture product identity, hardening priorities, cut list, and deployment focus. The historical commercial tier/pricing model (§3, §7) is retained for context only — Hangar is now pure MIT with no tiers, pricing, or license keys.
>
> **Reading order for "what is true now":** §2 (layering gate), §6 → Enforcement state, §9.
> §3, §5, §7 and §11 are dated snapshots kept for history; §4's "Current state" column is a
> 2026-03-24 snapshot superseded by §6. When this document and an ADR disagree, the ADR wins
> (`mcp-hangar/docs` → `adr/`, currently ADR-001…ADR-020).

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

All code in the repository is licensed under the MIT License. The `src/mcp_hangar/` package is a
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
| RBAC, API key auth, JWT/OIDC                       | `src/mcp_hangar/auth/`                        | Enterprise value, commercial differentiator                     |
| Tool Access Policies                               | `src/mcp_hangar/auth/`                        | Governance feature, commercial differentiator                   |
| Event sourcing persistence (SQLite/Postgres)       | `src/mcp_hangar/infrastructure/persistence/`  | Enterprise durability, commercial differentiator                |
| Compliance export (CEF/LEEF/JSON-lines/syslog)     | `src/mcp_hangar/compliance/`                  | Enterprise value                                                |
| Langfuse integration                               | `src/mcp_hangar/integrations/`                | Partner integration, commercial value                           |

### Architectural boundary (historical — the enterprise/core split is gone)

There is **one package**, `src/mcp_hangar/`. The former `enterprise/` tree was folded into it
before v1.0.0, and the machinery that policed the old boundary has since been removed:

- `tools/check_enterprise_imports.py` — **deleted** (the `tools/` directory no longer exists).
- Pre-commit hook `enterprise-import-boundary` — **removed**.
- CI job `import-boundary` in `security.yml` — **retired to a no-op** that echoes
  "Import boundary check retired — all code lives in `src/mcp_hangar/`".

What replaced it is a stronger, differently-shaped gate: a **hexagonal layering contract**
enforced by `import-linter` (`.importlinter`, CI step "Import contracts" in `ci-core.yml`,
guarded against an empty-contract false pass by `tests/unit/test_import_contracts.py`):

```
delivery         server : fastmcp_server : facade
infrastructure   infrastructure : metrics : http_client : stdio_client : retry : progress : gc
application      application
domain           domain
shared kernel    logging_config : lock_hierarchy : redactor : errors : protocol : context
                 : _sdk_compat : tasks_wire : negotiation : observability : trusted_hosts
```

A layer may import anything below it and nothing above. The contract carries a **debt
ledger** of edges that exist today and should not; it is capped so it can only shrink
(33 → 9 so far). Component packages (`auth`, `approvals`, `compliance`, `integrations`,
`bootstrap`, `observability`) carry their own internal layering and are deliberately out of
scope (`exhaustive = False`).

Note this gate polices *layering*, not the old core/enterprise split — that distinction no
longer exists anywhere in the tree. Bootstrap wiring in `server/bootstrap/` still loads
optional modules through dynamic `_import_attribute()` lookups; that is a
graceful-degradation pattern, not a licensing gate.

### Migration plan (historical — completed before v1.0.0)

The `src/mcp_hangar/` package absorbed former enterprise features before the v1.0.0 release.

---

## 3. Product Tiers

> **Historical (pre-2026 commercial model).** Hangar is now pure MIT: no tiers, no pricing, no
> license keys; all features are freely available and inbound=outbound. The tier, pricing, and
> consulting content below is retained for history and does **not** describe the current product.
> The Tier-0 capability list remains an accurate description of the (now universally available)
> feature set; the "Pro"/"Enterprise"/"Advisory" pricing and gating are superseded.

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
**Price target:** ~~$49-99/mo per cluster (or $499-999/yr)~~ — historical; free under MIT
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
**Price target:** ~~€2,000-5,000/mo or annual contract~~ — historical; free under MIT
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
**Price:** ~~€800-1,200/day~~ — historical; consulting model never operated

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

> **The "Current state" column is a 2026-03-24 snapshot and is stale.** NetworkPolicy
> generation and violation/enforcement signalling shipped; see
> [§6 → Enforcement state](#enforcement-state-reconciled-2026-08-11) for the reconciled
> picture. The "Target state" and "Priority" columns still read as intent.

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
| **Container network isolation**   | Docker MCP servers can talk to anything                                      | Default-deny egress, explicit allowlist                                                                     | v0.13.0        | **Done / shipped v1.0** (operator NetworkPolicy backend) |
| **Capability declaration schema** | No formal way to declare what a server needs                               | New `capabilities` config block                                                                             | v0.13.0        | **Done / shipped v1.0** (operator CRD `spec.capabilities.network.egress`) |
| **K8s NetworkPolicy generation**  | Operator doesn't create NetworkPolicies                                    | Auto-generate from CRD capabilities field                                                                   | v0.13.0        | **Done / shipped v1.0** (operator `pkg/networkpolicy`, reconciled per MCPServer) |
| **Licensing boundary**            | All code in MIT, no commercial protection                                  | Migrate Pro/Enterprise features into `src/mcp_hangar/`                                                     | v0.13.0        | Completed |
| **Behavioral baseline storage**   | No behavioral profiling exists                                             | Network connection logging per container                                                                    | v0.14.0        | |
| **Test coverage on auth**         | Auth stack is comprehensive but test density unclear                       | Audit test coverage, target 90%+ on auth paths                                                              | v0.13.0        | |
| **Security scanning in CI**       | Not visible in changelog                                                   | Trivy/Grype on container images, Semgrep on source                                                          | v0.13.0        | |
| **Dependency audit**              | Not visible                                                                | `pip-audit`, `npm audit` in CI, SBOM generation                                                             | v0.13.0        | |
| **OTEL semantic conventions**     | Governance telemetry is useful but not yet formalized as a stable contract | Define MCP-aware OTEL conventions for MCP server/tool/user/session/policy/enforcement attributes              | v0.13.0        | **DONE** (v6.0 Phase 31) |
| **Trace context propagation**     | Cross-system traces depend on ad hoc correlation                           | Standardize agent -> Hangar -> MCP server trace propagation for audit and enforcement paths                   | v0.13.0        | **DONE** (v6.0 Phase 32) |
| **Operator enforcement loop**     | Operator reconciles state, but not full governance posture                 | Make operator the primary engine for capability enforcement, NetworkPolicy rollout, and violation signaling | v0.13.0        | |
| **Admission/policy hooks**        | K8s integration is not yet policy-driven enough                            | Validate and reject unsafe specs before runtime using admission and policy integrations                     | v0.13.0        | |
| **Import boundary enforcement**   | No CI rule prevents core from importing enterprise                         | Add CI check: `src/` must never import from `src/mcp_hangar/`                                               | v0.13.0        | **DONE** (TASK-P0-1) |

### Important (before first paying customer)

| Area                           | Gap                                                                                | Action                                                                                       | Target version | Status |
|--------------------------------|------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|----------------|--------|
| **Helm chart security**        | Basic                                                                              | Pod Security Standards, network policies, RBAC scoping                                       | v0.14.0        | |
| **Upgrade path**               | No migration guide between versions                                                | Documented upgrade procedure, DB migration tooling                                           | v0.14.0        | |
| **Performance benchmarks**     | Batch benchmark exists, nothing else                                               | Latency overhead of proxy path, max MCP servers per instance                                   | v0.14.0        | |
| **Error handling audit**       | Exception hygiene improved in v0.11.0                                              | Full audit of error surfaces exposed to users                                                | v0.14.0        | |
| **OTLP completeness**          | Traces exist, but partner story needs explicit completeness across telemetry types | Ensure security-relevant traces, metrics, and logs/audit signals are exportable through OTLP | v0.14.0        | **DONE** (v6.0 Phase 33) |
| **Integration recipes**        | OTEL partner story is implied, not operationalized                                 | Publish reference deployments for OpenLIT, OTEL Collector, Langfuse, and Grafana             | v0.14.0        | **DONE** (v6.0 Phase 34) |
| **License key infrastructure** | No mechanism to activate Pro/Enterprise                                            | ~~Implement license key validation in bootstrap; enterprise modules load conditionally~~     | v0.14.0        | Dropped (pure MIT — no license keys; all modules load freely) |

### Nice-to-have (H2 2026)

| Area                               | Gap             | Action                                             |
|------------------------------------|-----------------|----------------------------------------------------|
| Cosign/notation image verification | Not implemented | Add to container MCP server startup path             |
| Seccomp profiles                   | Not shipped     | Create and ship default MCP server seccomp profile |
| Multi-cluster federation           | Not implemented | Design doc first, implement when demand validated  |
| SCIM provisioning                  | Not implemented | Enterprise tier only                               |

### Enforcement state (reconciled 2026-08-11)

Per the #295 enforcement audit (operator + helm-charts, read-only), the §6
"Not implemented" cells above were a stale 2026-03-24 snapshot. The 2026-07-01
reconciliation that replaced them has itself been overtaken twice — by ADR-010
(the agent/cloud tier retirement, which took ADR-006 with it) and by ADR-013
(the `MCPEgressPolicy` enforcement model). Current state:

- **NetworkPolicy L3/L4 egress enforcement shipped in v1.0**, implemented in the
  operator repo (`pkg/networkpolicy/builder.go`, reconciled with owner references
  in `internal/controller/mcpserver_controller.go`), driven by the CRD
  `spec.capabilities.network.egress` field plus an always-on CEL wildcard-egress
  gate and an off-by-default validating admission webhook. Still true.
- **Tetragon / kernel-level runtime enforcement is retired, not pending.**
  ADR-006 (Tetragon-first, pluggable backend) is **Superseded by ADR-010**:
  kernel-level enforcement only ever made sense delivered through the
  `hangar-agent` sidecar, and that sidecar plus the Hangar Cloud control plane
  were archived on 2026-07-16. The former **WS-9 (Tetragon) and WS-10 (forensic /
  provenance chain) workstreams are closed** — do not plan against them. Governance
  runs in-process in core (per-tenant projection, digest pinning, policy
  resolution on the call path) plus the operator.
- **The FQDN egress gap is closed.** Host/FQDN-only rules no longer emit a
  port-only (fail-open) NetworkPolicy: `builder.go` **fails closed** on them —
  no rule is emitted, the destination is denied, and the skipped rules are
  surfaced on the `AnnotationHostWarnings` annotation (operator #7, 2026-07-02).
  Enforceable FQDN allow-listing arrived with `MCPEgressPolicy` (operator v0.14.0):
  the Cilium flavor compiles declared upstreams into a `CiliumNetworkPolicy` with
  `toFQDNs` plus scoped kube-dns egress (`BuildEgressPolicyCiliumNetworkPolicy`),
  with CNI auto-detection between the Vanilla and Cilium flavors.
- **The current enforcement model is ADR-013**, not ADR-006: explicit-proxy L7
  enforcement in the data plane Hangar already operates, with a policy-*generated*
  L3/L4 network backstop (default-deny egress + allow-to-Hangar + allow-DNS in
  governed namespaces). No transparent TLS interception, no eBPF protocol parsing.
  Read ADR-013's status line before quoting its scope: the admission gate keys on
  the pod's self-declared `mcp-hangar.io/provider` label (a pod without it is
  admitted) and the image-digest check defaults to `warn`.

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

### Current layout

```
mcp-hangar/
├── LICENSE                    # MIT — applies to the entire repository
│
├── src/mcp_hangar/            # ONE package. MIT. No core/enterprise split.
│   ├── domain/                # DDD aggregates, value objects, events, contracts
│   │   └── contracts/         # Port interfaces implemented by the adapter layers
│   ├── application/           # CQRS commands, queries, handlers, sagas, read models
│   │   └── ports/             # Port interfaces implemented by the adapter layers
│   ├── infrastructure/        # Adapters (in-memory stores, Docker, K8s, OTEL)
│   │   └── persistence/       # SQLite/Postgres event stores, durable saga state
│   ├── server/                # REST API, WebSocket, CLI, tools, bootstrap
│   │   └── bootstrap/         # Composition root; optional modules via _import_attribute()
│   ├── fastmcp_server/        # MCP surface (SDK v2), interceptors, discover
│   ├── auth/                  # RBAC, API key stores, JWT/OIDC, rate limiter, auth API
│   ├── approvals/             # Approval gate workflow
│   ├── compliance/            # SIEM export (CEF, LEEF, JSON-lines, syslog)
│   ├── integrations/          # Langfuse adapter, future partner integrations
│   ├── observability/         # Tracing, metrics, audit pipeline
│   └── tasks_wire.py          # Vendored SEP-2663 task wire (ADR-015)
│
├── docs/                      # MkDocs documentation
│   └── internal/
│       └── PRODUCT_ARCHITECTURE.md  # This document
├── tests/                     # pytest: unit, integration, live (opt-in)
├── changelog.d/               # One changelog fragment per PR; CHANGELOG.md is generated
├── .importlinter              # Hexagon layering contract (see §2)
└── scripts/                   # Build, CI gates, migration
```

Note: ADRs do **not** live here. They live in the `mcp-hangar/docs` repository under
`adr/`; the conventions governing them are in `docs/internal/ADR_AGENTS.md`.

### Import boundary rule

The old core-vs-enterprise rule is gone (`tools/check_enterprise_imports.py` deleted, the
`import-boundary` CI job retired to a no-op, the pre-commit hook removed). The active rule
is the hexagonal layering contract in `.importlinter`, enforced by `lint-imports` in
`ci-core.yml` — see §2 for the layer stack and the shrink-only debt ledger.

---

## 9. MCP Surface Coverage — Deliberate Non-Interception

Hangar governs the MCP surfaces where runtime security and governance add value: `tools/*` (invocation, capability enforcement, behavioral profiling, RBAC, approval gates), plus lifecycle, health, and telemetry concerns.

Hangar deliberately does **not** intercept or govern the following MCP methods. This traffic passes through to upstream MCP servers unchanged, and upstream responses pass back unchanged:

- **Sampling** (`sampling/*`, e.g. `sampling/createMessage`) — server-initiated LLM completions handled by the client.
- **Roots** (`roots/*`, e.g. `roots/list`, `notifications/roots/list_changed`) — client-exposed filesystem/workspace roots.
- **MCP Logging** (`logging/setLevel`, `notifications/message`) — protocol-level log messages emitted by servers.

### Why this is by design

1. **These surfaces are deprecated upstream.** Under MCP spec 2026-07-28 (SEP-2577), Roots, Sampling, and MCP Logging are deprecated and annotation-only, with a 12-month migration window. Implementations MUST still handle them during that window, and new work SHOULD NOT adopt them. Building governance on top of a deprecated surface would be wasted effort with a known removal horizon; transparent pass-through keeps Hangar compatible without coupling to a sunsetting feature.
2. **Hangar's audit is OTEL and event-sourced, not MCP-`logging`-based.** The audit pipeline (`src/mcp_hangar/observability/`, `src/mcp_hangar/compliance/`) is built on OpenTelemetry traces and an event-sourced audit log, independent of the MCP protocol `logging` methods. Hangar does not need to intercept `logging/setLevel` or `notifications/message` to produce its own audit trail, and it does not repurpose that channel.

### Evidence

There is no interception handling for these methods in the codebase. Confirm with:

```bash
grep -rniE "sampling/|roots/|logging/setLevel|notifications/message" src/mcp_hangar
# (no matches)
```

### Multi-tenant isolation model

Two independent mechanisms are easy to conflate; they answer different questions.

- **Audience (`aud`)** validates that an inbound token was issued *for the Hangar resource server*, per RFC 8707. Hangar binds `aud` to a single, global Hangar resource URI (see `src/mcp_hangar/auth/bootstrap.py`). This is a resource-binding control — it proves "this token is for Hangar" — not a tenant-scoping control.
- **`tenant_id` + per-tenant projection** enforce cross-tenant separation. The `tenant_id` JWT claim identifies the calling tenant, and the per-tenant tool projection plus the member-scope access policy (#237 / #241) decide which backend tools that tenant may see and invoke.

There is deliberately no per-tenant audience. A caller carrying `tenant_id="A"` can never see or invoke another tenant's tools, because the projection read-model and the member-scope resolver filter every `tools/list` and every call by the caller's `tenant_id` — independently of the (shared) audience. This boundary is exercised by `tests/unit/test_cross_tenant_isolation.py`.

A per-tenant-resource-URI model (interpretation B), where each tenant would receive its own distinct `aud` value, was considered and deliberately deferred: it would push tenancy into the token-issuance and audience-validation path without strengthening the boundary that the `tenant_id` claim and per-tenant projection already enforce.

---

## 10. Competitive Intelligence — Key Gaps They Have

| Competitor               | What they lack (our opportunity)                                                                                                                                                                                                                                            |
|--------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Composio**             | No runtime behavior verification. Auth is their auth, not yours. No audit trail export. No K8s operator.                                                                                                                                                                    |
| **Smithery**             | "Config data is ephemeral" — zero runtime security. No governance. Community-submitted servers are unvetted.                                                                                                                                                                |
| **Glama**                | "Logging/traceability" is a bullet point, not a product. No behavioral profiling. No capability enforcement.                                                                                                                                                                |
| **OpenLIT**              | Excellent AI observability and MCP telemetry partner. Missing: MCP server lifecycle control, runtime enforcement, failover/group management, capability verification, and MCP-native governance semantics. We should integrate through OTEL, not imitate the product surface. |
| **MCP Gateway Registry** | Closest to us. Has audit logs, RBAC, OTLP telemetry. Missing: behavioral profiling, capability verification, semantic analysis, identity propagation. Their OTLP is generic; ours is MCP-aware.                                                                             |
| **CData Connect AI**     | Enterprise wrapper. Governance = their dashboard. No open source. No protocol-level understanding.                                                                                                                                                                          |

---

## 11. Decision Log

| Date       | Decision                                                                                                         | Rationale                                                                                                                                                                                                                                   |
|------------|------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 2026-03-24 | **~~BSL 1.1 for enterprise features, MIT for core.~~ Superseded: all code relicensed to MIT in v1.3.0.**         | Original: commercial protection. Dropped: complexity outweighed commercial returns. See epic #198.                                                                                                                                          |
| 2026-03-24 | **Enterprise/ directory migration before v0.13.0.**                                                              | Licensing boundary must be established before enterprise features are developed further. Retrofitting is harder than doing it right from the start.                                                                                         |
| 2026-03-24 | **~~CLA required for former enterprise contributions.~~ Dropped in v1.3.0.**                                   | No longer needed under single-MIT. Contributions flow inbound=outbound MIT.                                                                                                                                                                |
| 2026-03-23 | Docker/K8s first. Stdio is second-class for security features.                                                   | Runtime security requires container isolation. Period.                                                                                                                                                                                      |
| 2026-03-23 | Freeze Catalog API development.                                                                                  | Not our market. Discovery is Smithery/Registry.                                                                                                                                                                                             |
| 2026-03-23 | Integrate with OpenTelemetry-native observability tools (for example OpenLIT) instead of trying to replace them. | Win on governance and enforcement, not on copying generic AI observability platforms.                                                                                                                                                       |
| 2026-03-23 | Treat OTEL as a first-class product contract for partner integrations.                                           | Strong OTEL semantics let Hangar project governance telemetry into OpenLIT, Langfuse, Grafana, and other backends without product drift.                                                                                                    |
| 2026-03-23 | Kubernetes is the primary growth path; Docker follows, stdio is maintenance only.                                | Operator-driven governance, NetworkPolicy, admission, and violation handling are where defensible product value lives.                                                                                                                      |
| 2026-03-23 | Three-tier product model (Core/Pro/Enterprise).                                                                  | Need open source adoption funnel AND revenue path.                                                                                                                                                                                          |
| 2026-03-23 | v1.0.0 target: September 2026.                                                                                   | 6-month window before major vendors enter MCP observability.                                                                                                                                                                                |
| 2026-03-23 | Position as "runtime security," not "control plane."                                                             | "Control plane" is generic. "Runtime security and governance" is specific and defensible.                                                                                                                                                   |
| 2026-06-30 | Do not intercept or govern MCP `sampling/*`, `roots/*`, or `logging` methods; pass through unchanged.            | These surfaces are deprecated upstream (SEP-2577, spec 2026-07-28). Hangar's audit is OTEL/event-sourced, not MCP-`logging`-based. See section 9.                                                                                            |
| 2026-07-01 | Audience (`aud`) binds tokens to the shared Hangar RS (RFC 8707); cross-tenant isolation is enforced by the `tenant_id` claim plus per-tenant projection (#237 / #241), not by a per-tenant audience. Per-tenant-resource-URI (interpretation B) deferred. | A per-tenant `aud` would not strengthen a boundary already enforced by the projection read-model and member-scope policy; it would only complicate token issuance and audience validation. See section 9 and issue #312.                       |
