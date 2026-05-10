# ADR-006: Runtime Enforcement Strategy -- Tetragon-First, Pluggable Backend

**Status:** Accepted
**Date:** 2026-05-10
**Authors:** MCP Hangar Team

## Context

BLACKBIRD (codename for the MCP Hangar commercial platform) does not build
its own eBPF programs. It operates as an MCP-aware policy compiler that
emits CRDs for existing CNCF runtime security engines: Tetragon as the
primary backend, with KubeArmor and Falco as optional secondaries.
Differentiation lives in MCP semantics (workspace, user, tool, server,
workflow run), not in kernel hooks. Custom eBPF returns to the table no
earlier than v3+, if at all.

NetworkPolicy in v1.0 closes most SSRF vectors from a pod
(cluster-internal, IMDS, lateral movement, corporate network), but has
three known gaps:

1. **In-pod loopback** (`localhost:N`) -- does not traverse the CNI;
   NetworkPolicy never sees it.
2. **No process attribution** -- we know that pod X connected to Y, but
   not which thread of which MCP tool.
3. **Forensic-grade audit** -- non-repudiation for SOC 2 / EU AI Act /
   ISO 42001 requires binding PID -> cgroup -> container -> pod ->
   workspace -> user -> workflow run. NetworkPolicy provides only
   pod-IP-port granularity.

These gaps recur consistently in fresh CVE analysis (CVE-2026-44284
FastGPT; Semantic Kernel SSRF / process attribution gaps -- specific CVEs
pending public disclosure) and in the threat model presented to enterprise
prospects. They must be closed no later than v2.0, but not at the cost of
slipping the v1.0 launch.

## Decision

Pluggable backend architecture. The Hangar policy DSL is backend-agnostic.
Specific enforcement engines are implementations of an interface, not core
dependencies.

```text
MCP Policy DSL (Hangar Cloud)
        |
Policy Compiler (hangar-agent)
        |
+---------------+---------------+-------------+--------------+
| NetworkPolicy |   Tetragon    |  KubeArmor  |    Falco     |
|   (v1.0)      |   (v1.5+)     |   (v2.5+)   | (v2.5+, RO)  |
+---------------+---------------+-------------+--------------+
```

Tetragon wins as the primary backend because: native Kubernetes awareness,
kprobes on arbitrary kernel functions, enforcement actions (Sigkill /
Override / Signal / NotifyEnforcer) cover required semantics, in-kernel
filtering avoids userspace context switches, preserving Hangar's <5 ms p99
latency budget, installs standalone (does not require Cilium CNI),
Apache 2.0, CNCF Graduated (Cilium ecosystem), production-grade since
v1.x.

### Phasing

#### v1.0

- **Backend:** NetworkPolicy (L3/L4 egress) + tool registry + event
  sourcing.
- **Coverage:** cluster-internal services, IMDS, lateral pod-to-pod,
  corporate-network egress.
- **Known gaps (documented, not hidden):** in-pod loopback, no process
  attribution.
- **Pitch posture:** "Defense-in-depth today, deeper kernel integration on
  the roadmap. The gap is documented, not papered over."

#### v1.5

- **Backend:** Tetragon added behind a feature flag
  (`policy_engines: [networkpolicy, tetragon]`).
- **Translator:** MCP DSL -> TracingPolicy CRDs (kprobes on
  `tcp_connect`, `sk_alloc`, `execve`, `openat`).
- **Capability gain:** loopback enforcement, process attribution in audit
  events.
- **Validation gate:** Talos+k3s green + at least one design partner
  running it in production.

#### v2.0

- **Tetragon backend GA**, feature parity with NetworkPolicy (or
  surpassing it).
- **Forensic-grade audit:** complete provenance chain PID -> cgroup ->
  container -> pod -> workspace -> user -> workflow run ID.
- **BSL 1.1 Enterprise gating:** Tetragon backend and forensic chain in
  Enterprise tier; OSS Agent stays on NetworkPolicy + tool registry.
- **MCP DSL:** unified expressiveness -- protocol-level (workspace, tool,
  server) and process-level (binary, capability, syscall) constraints in a
  single policy.

#### v2.5+

- **KubeArmor backend** for the LSM-first audience (defense, government,
  regulated).
- **Falco backend** in detect-only mode for organizations that already run
  Falco and want to avoid a second agent.
- **Cross-cluster federation** -- separate workstream, not a gating
  concern for this ADR.

## Consequences

### Positive

- Backend-agnostic DSL prevents lock-in to any single enforcement engine.
- Tetragon closes loopback and process attribution gaps without custom eBPF
  maintenance burden.
- Phased rollout avoids delaying the v1.0 launch.
- Apache 2.0 licensing and CNCF Graduated status reduce governance risk.
- In-kernel filtering preserves <5 ms p99 latency budget.
- MCP-semantic differentiation (workspace, user, tool, server, workflow
  run) is defensible against competitors who replicate the same kernel
  hooks.

### Negative

- **Dependency on Tetragon being installed in the customer cluster.**
  Mitigation: Helm sub-chart that installs it for them, plus
  bring-your-own-Tetragon mode with detect-and-warn. Not a blocker -- most
  Kubernetes-native enterprises already run some runtime security layer.
- **Cisco / Isovalent governance risk.** Apache 2.0 protects against the
  worst case. Backend-agnostic DSL prevents lock-in. If Tetragon stalls,
  we refactor to KubeArmor in roughly six months.
- **Differentiation must live above the kernel.** A competitor can build
  the same translator on Tetragon. Defensible position: MCP semantics,
  ecosystem fluency, threat model -- not kernel access. Anyone can write
  BPF, and customer security teams know it.

## Alternatives Considered

### 1. Custom eBPF agent

- **Rejected**: Order-of-magnitude more engineering, BPF verifier
  constraints, kernel compat matrix, `CAP_BPF` in our agent as additional
  attack surface, no defensible moat at the kernel layer. Returns to
  consideration only if Tetragon-grade primitives become a blocker (today
  they are not).

### 2. OPA Gatekeeper / Kyverno as runtime backend

- **Rejected**: Admission-time only. May coexist for admission concerns
  but cannot replace Tetragon for runtime.

### 3. Pure userspace proxy

- **Rejected**: Wrapper-tax problem (known issue with Composio, Cloudflare
  AI Gateway), defeated by L3/L4 enforcement already at v1.0, adds latency
  in the hot path.

## References

- [ADR-005](ADR-005-sep-1763-interceptor-compliance.md) -- SEP-1763
  Interceptors operate at L7/MCP protocol level (JSON-RPC
  validators/mutators); Tetragon operates at L3/L4 + syscall level. No
  functional overlap -- complementary layers.
- CVE-2026-44284 (FastGPT SSRF) -- motivating gap analysis.
- Semantic Kernel SSRF / process attribution gaps -- specific CVEs pending
  public disclosure.
