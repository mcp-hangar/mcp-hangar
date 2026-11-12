# MCP Hangar Strategy

> **Maintainer:** [@mapyr](https://github.com/mapyr)
>
> This document is the **product strategy and positioning** for MCP Hangar: what it is,
> who it is for, the principles that govern how it is built, and what it will deliberately
> never become. It is intentionally stable and rarely changes.
>
> **Delivery status and near-term plans live elsewhere** — what has shipped is in
> [CHANGELOG.md](CHANGELOG.md), and forward-looking work is tracked as
> [GitHub issues and milestones](https://github.com/mcp-hangar/mcp-hangar/issues).
> This file is not a delivery tracker and should not accumulate per-release status tables.

## Vision

MCP Hangar is **a policy enforcement plane for MCP: it pins every tool, holds risky calls for approval, and enforces egress policy**.

In MCP, the tool list is a hint the client caches; the call path is the only surface a gateway mediates in real
time. Hangar sits on that path and gives every call three things:

- **A deterministic verdict.** Allow, deny or hold for approval, decided from declared policy: tool access lists,
  per-tenant tool projection, digest pins and L7 egress rules. No anomaly scores and no learned baselines, so every
  verdict is reproducible from the policy that produced it.
- **Tool-contract integrity.** A tool whose `name`, `description`, `inputSchema` or `outputSchema` no longer matches
  its pin is withheld instead of quietly serving a different tool (pins block by default), and
  `mcp-hangar pin --check` reports the drift in CI.
- **An attributable audit record.** A call is attributed to the principal it arrived with, and a refusal leaves an audit record that
  `mcp-hangar why` can look up by correlation id and that the SIEM and OTLP exports carry.

Two parts of that promise need a step from whoever runs the gateway, and the product says so rather than implying
otherwise:

- **Pinning is opt-in.** `mcp-hangar init` records a pin for every tool of the servers it moves behind Hangar; a
  hand-written config gets pins from `mcp-hangar pin --write`. A tool with no pin is served unpinned.
- **Egress policy has two halves.** The tool and argument rules are pushed to core over its admin API, normally by
  the Kubernetes operator from an `MCPEgressPolicy`, and core enforces them on every call. The network half, a
  `NetworkPolicy` that limits what the server's pod can reach, is written by the operator and exists only on
  Kubernetes.

## Who it is for

### Entry user: a developer with a client config

A developer with five to fifteen MCP servers in Claude Code, Cursor or Claude Desktop. The first value is a deny on
drift, on their own servers, in about five minutes on one laptop: install, `mcp-hangar init -y`, restart the client,
and a tool whose description changes underneath them is withheld. CI runs the README quickstart every day on a clean
runner, and from `curl` to the withheld tool it takes about 40 seconds plus the client restarts.

For this user stdio is the transport that matters. Over stdio the process that spawned Hangar is the trust boundary,
so `auth.stdio.principal` declares the caller
([ADR-026](https://mcp-hangar.io/docs/adr/ADR-026-stdio-is-an-authenticated-transport)), and pins, tool access
lists, approvals and audit apply over stdio too.

### Expansion user: a platform team with a fleet

A platform team that needs identity, approvals and egress policy across many MCP servers and many callers, on
Kubernetes. Core adds API-key and OIDC/JWT identity with role-based access, per-tenant tool projection that fails
closed on unknown identity, and approvals attributed to a real principal. The operator runs container-mode servers,
registers them in core, compiles `MCPEgressPolicy` into core's tool rules and writes the `NetworkPolicy` backstop.

That path works from the published charts. The
[Kubernetes Quick Start](https://mcp-hangar.io/docs/getting-started/kubernetes-quickstart) goes from an empty kind
cluster to a tool call refused by an `MCPEgressPolicy`, and a nightly job runs it as written against the published
charts.

The entry user and the expansion user run the same core package and the same policy model. Where other MCP gateways
are stronger is listed, with sources, at [mcp-hangar.io/compare](https://mcp-hangar.io/compare).

## Design principles

1. **Deterministic over clever.** A verdict comes from policy a person wrote, never from a model, a score or a
   learned baseline. If a decision cannot be explained by pointing at the rule that made it, it does not ship.
2. **Fail closed at trust boundaries.** In front-door mode an unknown identity gets no tools, a drifted pin withholds
   the tool, and HTTP on a non-loopback interface refuses to start without authentication. A claim in the docs must
   match what the mechanism delivers.
3. **Pin, then verify.** A tool's contract is the digest a person approved. Each listing and call is checked against
   it, and a change is adopted only by writing a new pin.
4. **Every call is attributable.** Identity is not a premium feature bolted on later. API keys, OIDC, roles and an
   audit record with a principal are part of the base product: verified by key or token over HTTP when auth is on,
   declared by the spawning process over stdio.
5. **Laptop first, fleet ready.** The first contact is a developer's own client config. Kubernetes is where a fleet
   grows, and the operator is how Hangar works with the platform there: reconciled custom resources and generated
   network policy, not a parallel control loop.
6. **Governance over metrics.** OpenTelemetry traces, Prometheus metrics and structured logs exist to support policy,
   verification, audit and response. Telemetry leaves Hangar over open interfaces (OTLP and standard SIEM formats), so
   any compatible backend can read it.

## What we won't build

- **MCP server hosting.** Hangar governs servers you run; it does not host them for you.
- **MCP server marketplace.** The official MCP Registry exists. Hangar is listed in it and does not replace it.
- **LLM runtime.** Hangar sits between the agent and its MCP servers. It does not run the model.
- **Generic API gateway.** Hangar is purpose-built for MCP. HTTP APIs belong behind a general-purpose gateway.
- **Generic AI observability platform.** Prompt hubs, playgrounds, model-eval suites and telemetry dashboards are
  adjacent tools. Hangar exports to them instead of competing with them.
- **ML guardrails or detection.** No prompt-injection classifiers, anomaly scoring or behavioral baselines in the
  verdict path. A verdict that depends on a model is not reproducible, and reproducibility is the product.

## License

MCP Hangar is MIT-licensed: no open-core edition, no paid tier, no SaaS. Hangar is self-hosted only. The name and
logo are licensed separately; see the [README](README.md#name-and-logo).

## Contributing

Contributions are welcome, especially in:

- `MCPEgressPolicy` and network policy templates for common MCP servers
- Kubernetes operator enhancements
- Client support for `mcp-hangar init`
- Approval delivery channels, as installable entry points rather than vendor code in core
- Documentation and guides

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Links

- **Website:** [mcp-hangar.io](https://mcp-hangar.io)
- **GitHub:** [github.com/mcp-hangar/mcp-hangar](https://github.com/mcp-hangar/mcp-hangar)
- **Blog:** [whyisthisdown.com](https://whyisthisdown.com)
- **Newsletter:** [Buttondown](https://buttondown.com/whyisthisdown)
