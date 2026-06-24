# MCP Hangar Strategy

> **Maintainer:** [@mapyr](https://github.com/mapyr)
>
> This document is the **product strategy and positioning** for MCP Hangar: what it is,
> the principles that govern how it is built, and what it will deliberately never become.
> It is intentionally stable and rarely changes.
>
> **Delivery status and near-term plans live elsewhere** — what has shipped is in
> [CHANGELOG.md](CHANGELOG.md), and forward-looking work is tracked as
> [GitHub issues and milestones](https://github.com/mcp-hangar/mcp-hangar/issues)
> (epics carry the full scope, e.g. per-tenant tool projection and the OAuth Resource
> Server handshake). This file is not a delivery tracker and should not accumulate
> per-release status tables.

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
4. **Open core, open telemetry.** The entire project is open source under MIT. All telemetry flows through OpenTelemetry into partner
   backends such as OpenLIT, Langfuse, Grafana, or a standard OTEL Collector. No vendor lock-in on the observation path.
   OTEL is a strategic integration interface, not a product pivot.
5. **Governance over metrics.** Metrics, traces, and telemetry exist to support policy, verification, audit, and response.
   Hangar is not trying to become a generic AI observability platform.
6. **Work with the platform, not around it.** Operator-driven reconciliation, admission hooks, NetworkPolicy generation,
   policy engines, and violation signals are the preferred implementation path for enforcement.
7. **Enterprise-grade from day one.** RBAC, audit trails, API key rotation, OIDC — these aren't premium features bolted
   on later. They're foundational.

## Licensing

MCP Hangar is licensed under the [MIT License](LICENSE). The full source is always
readable, auditable, and free to use, modify, and distribute without restriction.

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

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Links

- **Website:** [mcp-hangar.io](https://mcp-hangar.io)
- **GitHub:** [github.com/mcp-hangar/mcp-hangar](https://github.com/mcp-hangar/mcp-hangar)
- **Blog:** [whyisthisdown.com](https://whyisthisdown.com)
- **Newsletter:** [Buttondown](https://buttondown.com/whyisthisdown)
