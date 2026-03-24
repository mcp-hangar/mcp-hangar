# MCP Hangar Roadmap

> **Last updated:** 2026-03-24
> **Status:** Active development
> **Maintainer:** [@mapyr](https://github.com/mapyr)

## Vision

MCP Hangar is the **runtime security and governance layer for MCP servers**.

Every organization deploying MCP servers in production faces the same question: *"What are my agents actually doing with
these tools?"* MCP Hangar answers it — lifecycle management, behavioral governance, identity-aware audit trails, and
runtime security enforcement in a single deployable control plane.

The primary battlefield is Kubernetes. Docker remains a first-class local and transitional deployment target, but new
governance and enforcement work should be designed for the Kubernetes ecosystem first and then adapted to Docker where
practical.

OpenTelemetry-compatible observability backends such as `OpenLIT`, `Langfuse`, Grafana, or other OTLP destinations can
extend the visibility layer around Hangar. They are partners and integrations, not the product category Hangar is trying
to become.

Composio connects agents to tools. Smithery helps you find servers. Glama hosts them. **Hangar makes sure they behave.**

## Design Principles

1. **Observe everything, trust nothing.** MCP servers are black boxes. Hangar treats them as untrusted workloads —
   monitor behavior, enforce boundaries, audit everything.
2. **Kubernetes-first, Docker-compatible.** Runtime security requires isolation and policy enforcement close to the
   orchestrator. Kubernetes is the primary growth path. Docker remains important for local development, smaller
   deployments, and compatibility. Stdio providers are supported but do not receive new security/governance investments.
3. **Declare, then verify.** Servers declare what they need. Hangar verifies at runtime that they don't exceed those
   declarations. Deviation = alert + block.
4. **Open core, open telemetry.** The core control plane is open source under MIT. Advanced governance, enforcement, and
   compliance capabilities live under [BSL 1.1](enterprise/LICENSE.BSL) — source-available, free for evaluation and
   development, commercially licensed for production use. All telemetry flows through OpenTelemetry into partner
   backends such as OpenLIT, Langfuse, Grafana, or a standard OTEL Collector. No vendor lock-in on the observation path.
   OTEL is a strategic integration interface, not a product pivot.
5. **Governance over dashboards.** Metrics, traces, and UI exist to support policy, verification, audit, and response.
   Hangar is not trying to become a generic AI observability platform.
6. **Work with the platform, not around it.** Operator-driven reconciliation, admission hooks, NetworkPolicy generation,
   policy engines, and violation signals are the preferred implementation path for enforcement.
7. **Enterprise-grade from day one.** RBAC, audit trails, API key rotation, OIDC — these aren't premium features bolted
   on later. They're foundational.

## Licensing

MCP Hangar uses a **dual-license model**:

- **Core** (`src/mcp_hangar/`, `packages/operator/`, `packages/helm-charts/`) — [MIT License](LICENSE). Free to use,
  modify, and distribute without restriction.
- **Enterprise** (`enterprise/`) — [Business Source License 1.1](enterprise/LICENSE.BSL). Source-available: anyone can
  read, audit, and run the code for evaluation, development, and testing. Production use requires a commercial license.
  Each release automatically converts to MIT after the Change Date (3 years from release).

This means:

- The full source is always readable and auditable — no black boxes.
- Core features are permanently free and open source.
- Enterprise features are protected for 3 years per release, then become fully open (MIT).
- You always have access to the code. You pay for the right to run enterprise features in production.

See [LICENSE](LICENSE) and [enterprise/LICENSE.BSL](enterprise/LICENSE.BSL) for full terms.

---

## Current State (v0.12.0)

### What's production-ready

- **Provider Lifecycle Management** — State machine (COLD → INITIALIZING → READY → DEGRADED → DEAD) with circuit
  breaker, health checks, automatic failover
- **Provider Groups** — Load balancing (round-robin, weighted, least-connections, random, priority/failover),
  group-level circuit breaker, automatic retry
- **Authentication & Authorization** — API key auth with rotation and grace periods, JWT/OIDC with JWKS, RBAC (admin,
  provider-admin, developer, viewer, auditor), timing-attack-safe key validation
- **Tool Access Policies** — Glob-pattern allow/deny lists, 3-level policy merge (provider → group → member), runtime
  enforcement
- **Observability** — OpenTelemetry distributed tracing, Prometheus metrics (40+ metrics), Grafana dashboards, Langfuse
  LLM observability, structured audit logging, OTLP export into partner backends such as OpenLIT
- **Event Sourcing** — Full event store (SQLite, Postgres, in-memory), saga persistence with compensation, snapshot
  support
- **Batch Invocations** — Parallel tool execution, single-flight cold starts, circuit breaker integration, response
  truncation with continuation cache
- **REST API & Dashboard** — Full REST API with WebSocket streaming, React 19 dashboard with metrics, topology, RBAC
  management, log viewer
- **Kubernetes Operator** — MCPProvider, MCPProviderGroup, MCPDiscoverySource CRDs, Helm charts, state-machine
  reconciliation
- **Hot Reload** — File watching (watchdog/polling), SIGHUP, MCP tool trigger, intelligent diff (only changed providers
  restart)

### What's experimental

- Catalog API and discovery registry (shipped in v0.12.0, API/stability not yet guaranteed)
- Config export/backup system (shipped in v0.12.0, UX/workflow still settling)
- D3 force-graph topology visualization (shipped in v0.12.0, visualization model still experimental)

---

## Roadmap: H1 2026 (April – September)

### Cross-cutting foundation: OpenTelemetry interoperability

These items support all phases below and exist to make Hangar's governance telemetry portable into partner platforms
such as OpenLIT without turning Hangar into a generic observability product.

| Item                                                   | Description                                                                                                                                                                                                                    | Priority |
|--------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------|
| **MCP-aware OTEL semantic conventions**                | Standardize span/log/metric attributes for `provider`, `tool`, `group`, `user`, `session`, `policy`, `enforcement_action`, and `risk_signal` so partner backends receive governance-grade telemetry instead of generic traces. | P0       |
| **End-to-end trace context propagation**               | Preserve trace context across agent → Hangar → provider boundaries so runtime decisions, policy checks, and provider actions stay correlated in downstream OTEL backends.                                                      | P0       |
| **OTLP completeness across traces, metrics, and logs** | Ensure security-relevant events are exportable through OTLP, including audit signals, health/state transitions, policy decisions, and enforcement outcomes.                                                                    | P1       |
| **Integration recipes and reference deployments**      | Ship docs and examples for OTEL Collector, OpenLIT, Langfuse, and Grafana-based deployments as enablement material, not core product surface area.                                                                             | P1       |

### Phase 1: Kubernetes Enforcement Foundation (April – May)

**Goal:** Make Hangar the most secure way to run MCP servers in Kubernetes, with Docker kept compatible for local and
smaller-scale deployments.

*Phase 1 features are developed under [MIT](LICENSE) in the core codebase. Capability declaration and enforcement are
foundational — they must be open for trust and adoption.*

| Item                                      | Description                                                                                                                                                                                                               | Priority |
|-------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------|
| **Network Policy Enforcement**            | Declare allowed egress destinations per provider. Hangar generates and enforces network policies (K8s NetworkPolicy first, Docker network rules where practical). Undeclared outbound connections are blocked and logged. | P0       |
| **Capability Declaration Schema**         | Extend provider config with `capabilities` block: required network destinations, filesystem paths, environment variables, expected tool schemas. Machine-readable, auditable.                                             | P0       |
| **Operator-Driven Policy Reconciliation** | Push capability declarations into the operator so policy state, provider state, and enforcement state reconcile through Kubernetes-native resources.                                                                      | P0       |
| **Admission and Policy Integration**      | Integrate with admission and policy tooling so invalid or unsafe provider specs can be rejected before runtime.                                                                                                           | P0       |
| **Runtime Capability Verification**       | Compare declared capabilities against observed behavior. Alert on deviation. Optional hard-block mode.                                                                                                                    | P0       |
| **Violation Signals**                     | Emit explicit violation and enforcement signals for denied egress, capability drift, policy rejection, and quarantine actions.                                                                                            | P0       |
| **Container Filesystem Sandboxing**       | Read-only root filesystem by default. Explicit mount allowlist. Blocked sensitive paths hardened beyond current implementation.                                                                                           | P1       |
| **Seccomp / AppArmor Profiles**           | Ship default security profiles for MCP server containers. Reduce syscall surface to minimum required.                                                                                                                     | P1       |
| **Image Provenance Verification**         | Verify container image signatures (cosign/notation) before starting providers. Reject unsigned images in strict mode.                                                                                                     | P2       |

### Phase 2: Behavioral Profiling (May – June)

**Goal:** Know what your MCP servers do — not what they say they do.

*Behavioral profiling features are developed under [BSL 1.1](enterprise/LICENSE.BSL) in the `enterprise/behavioral/`
directory.*

| Item                               | Description                                                                                                                                         | Priority |
|------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|----------|
| **Network Behavioral Baseline**    | Profile outbound connection patterns per provider during learning period. DNS queries, destination IPs/domains, request frequency. Store baseline.  | P0       |
| **Behavioral Deviation Detection** | Compare runtime network behavior against baseline. Alert on new destinations, unusual frequency, unexpected protocols.                              | P0       |
| **Tool Schema Drift Detection**    | Detect when an MCP server changes its advertised tools/schemas between restarts. Alert on additions, removals, or parameter changes.                | P0       |
| **Resource Usage Profiling**       | Track CPU, memory, network I/O per provider. Detect anomalous resource consumption patterns.                                                        | P1       |
| **Behavioral Report Export**       | Generate per-provider behavioral reports: observed network destinations, tool usage patterns, resource consumption. PDF/JSON export for compliance. | P1       |

### Phase 3: Identity Propagation & Audit (June – July)

**Goal:** Full chain-of-custody from user to agent to tool call to result.

*Identity propagation and compliance export features are developed under [BSL 1.1](enterprise/LICENSE.BSL) in
the `enterprise/identity/` and `enterprise/compliance/` directories.*

| Item                                    | Description                                                                                                                                                                     | Priority |
|-----------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------|
| **Caller Identity Injection**           | Propagate user/agent/session identity through every tool invocation. MCP protocol extension via metadata headers.                                                               | P0       |
| **Identity-Aware Audit Trail**          | Extend event store: every tool call records WHO (user), THROUGH (agent), IN (session), CALLED (tool), WITH (params hash), GOT (result status).                                  | P0       |
| **Compliance Export**                   | Audit trail export in formats consumed by enterprise SIEM/compliance (CEF, LEEF, JSON-lines).                                                                                   | P1       |
| **Cost Attribution**                    | Track token consumption and API call costs per user, per agent, per provider. FinOps dashboard view.                                                                            | P1       |
| **Consent & Data Classification**       | Tag tool invocations with data sensitivity level. Flag calls that access PII, financial data, or regulated information.                                                         | P2       |
| **OTEL identity and policy attributes** | Export user/session/provider/tool/policy metadata through OTEL spans, metrics, and logs so downstream systems can correlate runtime governance decisions with identity context. | P1       |

### Phase 4: Semantic Analysis (August – September)

**Goal:** Detect malicious or anomalous agent behavior patterns.

*Semantic analysis features are developed under [BSL 1.1](enterprise/LICENSE.BSL) in the `enterprise/semantic/`
directory.*

| Item                             | Description                                                                                                                                                                  | Priority |
|----------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------|
| **Call Sequence Pattern Engine** | Define and detect multi-step patterns across tool calls. Example: `list_files` → `read_file(credentials.*)` → `send_email(external)` = exfiltration chain.                   | P0       |
| **Pre-built Detection Rules**    | Ship default rule pack: credential exfiltration, privilege escalation, data staging, reconnaissance patterns.                                                                | P1       |
| **Custom Rule DSL**              | Allow operators to define custom detection rules. YAML-based, event-sourced rule versioning.                                                                                 | P1       |
| **Agent Behavior Scoring**       | Per-agent trust score based on historical behavior. Anomaly score per session. Dashboard integration.                                                                        | P2       |
| **Automated Response Actions**   | On pattern match: alert, throttle, suspend agent session, block provider. Configurable response playbooks.                                                                   | P2       |
| **OTEL risk taxonomy**           | Publish anomaly, rule-match, and enforcement signals as a stable OTEL-friendly taxonomy so partner backends can alert, search, and correlate on runtime governance outcomes. | P1       |

---

## Roadmap: H2 2026 (Exploration)

These items are on the horizon but not committed. Community input welcome.

- **MCP Server Card Integration** — Consume and verify Server Cards as capability declarations
- **Multi-Cluster Federation** — Federated governance across multiple Hangar instances
- **Supply Chain Security** — SBOM generation for MCP server images, vulnerability scanning
- **Agent-to-Agent Governance** — Extend behavioral profiling to A2A (Agent2Agent) protocol interactions
- **Managed Cloud Offering** — Hosted Hangar with multi-tenant isolation
- **OpenTelemetry partner blueprints** — Battle-tested deployment recipes for Hangar with OpenLIT, OTEL Collector,
  Langfuse, and Grafana stacks
- **Kubernetes Policy Ecosystem Integration** — Deeper integration with policy engines and admission layers for
  cluster-native enforcement workflows

---

## What We Won't Build

- **MCP server hosting.** Smithery, Glama, and Composio do this. We govern, we don't host.
- **MCP server marketplace.** The official MCP Registry exists. We integrate with it, not replace it.
- **LLM runtime.** Hangar sits between the agent and MCP servers. We don't run the model.
- **Generic API gateway.** Hangar is purpose-built for MCP. Use Kong, Envoy, or Traefik for HTTP APIs.
- **Generic AI observability platform.** Prompt hubs, playgrounds, broad model-eval suites, and vendor-specific
  telemetry UX are useful adjacent tools, but not Hangar's product lane. We integrate with platforms such as OpenLIT
  instead of trying to replace them.
- **New stdio-focused security features.** Stdio remains supported, but future governance and enforcement investment is
  centered on Kubernetes first and Docker second.

---

## Contributing

We welcome contributions to the **MIT-licensed core**, especially in:

- Network policy templates for common MCP servers
- Kubernetes operator enhancements
- Admission / policy integration for Kubernetes
- Violation and enforcement signal modeling
- Detection rule packs for semantic analysis
- Behavioral profiling improvements (core interfaces and contracts)
- Documentation and guides

Contributions to the `enterprise/` directory (BSL-licensed) require a [Contributor License Agreement](CLA.md) to
maintain licensing flexibility. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Links

- **Website:** [mcp-hangar.io](https://mcp-hangar.io)
- **GitHub:** [github.com/mcp-hangar/mcp-hangar](https://github.com/mcp-hangar/mcp-hangar)
- **Blog:** [whyisthisdown.com](https://whyisthisdown.com)
- **Newsletter:** [Buttondown](https://buttondown.com/whyisthisdown)
