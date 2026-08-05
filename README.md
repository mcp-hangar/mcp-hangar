# MCP Hangar

**The policy enforcement plane for MCP -- deterministic admission and egress policy, attributable audit, and SIEM export for your MCP server fleet. MIT, self-hosted, no SaaS.**

[![PyPI](https://img.shields.io/pypi/v/mcp-hangar)](https://pypi.org/project/mcp-hangar/)
[![CI](https://github.com/mcp-hangar/mcp-hangar/actions/workflows/ci-core.yml/badge.svg)](https://github.com/mcp-hangar/mcp-hangar/actions/workflows/ci-core.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Why

In MCP, the tool list is a hint the client caches; the call path is the only surface a provider mediates in real time. Every governance primitive worth having -- revocation, per-tenant scoping, audit -- attaches there, or attaches to nothing. Hangar puts a policy enforcement plane on that seam: one mediated path for lifecycle, policy, and telemetry across your whole MCP server fleet.

> Background: [The Advisory List -- Why MCP Governance Lives at the Call Path](https://whyisthisdown.com/posts/the-advisory-list)

## Install

```bash
pip install mcp-hangar
# or: uv pip install mcp-hangar
```

This resolves to **2.0.0**. Coming from 1.6.x, read the
[upgrade guide](https://mcp-hangar.io/docs/upgrade) first — two of the changes
need a decision before you upgrade, not after: Slack approval delivery now needs
an adapter you run yourself, and approval resolution is authorized. Your upstream
MCP servers do **not** have to move; a connection that negotiates the 2025-11-25
protocol keeps working. To stay on the old line while you plan, pin
`"mcp-hangar>=1.6,<2"` — note that it is closed and receives no fixes.

## Quickstart

Point Hangar at an MCP server in `config.yaml`:

```yaml
mcp_servers:
  github:
    mode: subprocess
    command: [uvx, mcp-server-github]
    env:
      GITHUB_TOKEN: ${GITHUB_TOKEN}
```

Then serve it:

```bash
mcp-hangar serve --config config.yaml                     # stdio (Claude Desktop)
mcp-hangar serve --config config.yaml --http --port 8000  # HTTP + REST API at /api/
```

> Hangar refuses to bind a non-loopback interface without auth. For a
> quick/insecure demo, pass `--unsafe-no-auth`; for anything real, configure
> the `auth` block.

Or skip the config entirely -- get filesystem, fetch, and memory servers wired into Claude Desktop in one line:

```bash
curl -sSL https://mcp-hangar.io/install.sh | bash && mcp-hangar init -y && mcp-hangar serve
```

## What you get

The enforcement plane — what the call path actually decides:

- **L7 egress policy** -- allow/deny in MCP semantics: which upstream, which tool, which arguments. Deterministic, with no anomaly scores and no learned baselines, so every verdict is reproducible from the policy that produced it.
- **Tool-schema digest pinning** -- an upstream that changes a pinned tool's schema fails closed instead of quietly serving a different tool.
- **Auth & RBAC** -- API-key and OIDC/JWT identity with role-based access and RFC 8707 audience binding; bootstrap the first administrator with `mcp-hangar auth bootstrap-admin`, and every call carries a verified principal into the audit trail.
- **Per-tenant tool projection** -- front-door mode presents a different executable surface per caller, fail-closed on unknown identity.
- **Human-in-the-loop approvals** -- gate a call on an explicit decision, authorized and attributed to a real principal. Delivery channels are pluggable; core ships no vendor integration.
- **Governed task relay** -- Hangar interposes on the SEP-2663 task lifecycle and never becomes an executor: no scheduler, no job runner, no result store.
- **Attributable audit** -- an identity-attributed audit record exported to SIEM as CEF, LEEF 2.0, RFC 5424 syslog or JSON-lines, and to OTLP.

Everything else it takes to run a fleet:

- **Parallel tool calls** -- one `hangar_call` fans out to many MCP servers concurrently; all results returned together.
- **Lifecycle management** -- lazy start, health checks, single-flight cold starts, idle shutdown, and per-server circuit breaking.
- **Hot config reload** -- add or withdraw servers and tools via file watch, no restart.
- **OAuth ingress** -- advertise as an RFC 9728 protected resource and challenge external agents for verified tokens.
- **Observability built in** -- OpenTelemetry traces, Prometheus metrics, and structured logs.

## One config gotcha: `tools:` is overloaded

The per-server `tools:` key accepts two forms that look similar and mean
opposite things:

```yaml
tools:                        # LIST -- pre-start visibility projection
  - name: add
    inputSchema: { type: object, properties: { a: { type: number } } }

tools:                        # DICT -- access policy
  allow: [create_issue, list_issues]
  deny: [delete_repository]
```

The **list** form only lets a tool be listed before its provider has started.
It is **not** an access policy, and it does not survive startup: the provider's
dynamic `tools/list` is authoritative and replaces it entirely, so a
statically-listed tool the provider does not return becomes uncallable and
fails with `Tool not found: <name>` at invocation.

The **dict** form is the access policy — glob patterns, three-level merge.
Reach for it when you mean to restrict something. Full semantics in the
[configuration reference](https://mcp-hangar.io/docs/reference/configuration).

## Documentation

- [Getting Started](https://mcp-hangar.io/docs/getting-started/quickstart) &middot; [Configuration](https://mcp-hangar.io/docs/reference/configuration) &middot; [Python API](https://mcp-hangar.io/docs/guides/FACADE_API)
- [Governance & Front Door](https://mcp-hangar.io/docs/guides/FRONT_DOOR) &middot; [Authentication & RBAC](https://mcp-hangar.io/docs/guides/AUTHENTICATION) &middot; [Observability](https://mcp-hangar.io/docs/guides/OBSERVABILITY)
- [Kubernetes operator](https://github.com/mcp-hangar/mcp-hangar-operator) &middot; [Helm charts](https://github.com/mcp-hangar/helm-charts) &middot; [All docs](https://mcp-hangar.io/docs)
- [Release compatibility matrix](https://github.com/mcp-hangar/docs/blob/main/operations/RELEASE_COMPATIBILITY.md) &middot; which core, operator, and chart versions are released and tested together

## License

[MIT](LICENSE)
