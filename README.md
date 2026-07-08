# MCP Hangar

**Open-source control plane for MCP servers -- lifecycle, governance, and observability for your server fleet.**

[![PyPI](https://img.shields.io/pypi/v/mcp-hangar)](https://pypi.org/project/mcp-hangar/)
[![CI](https://github.com/mcp-hangar/mcp-hangar/actions/workflows/ci-core.yml/badge.svg)](https://github.com/mcp-hangar/mcp-hangar/actions/workflows/ci-core.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Why

In MCP, the tool list is a hint the client caches; the call path is the only surface a provider mediates in real time. Every governance primitive worth having -- revocation, per-tenant scoping, audit -- attaches there, or attaches to nothing. Hangar puts a control plane on that seam: one mediated path for lifecycle, policy, and telemetry across your whole MCP server fleet.

> Background: [The Advisory List -- Why MCP Governance Lives at the Call Path](https://whyisthisdown.com/posts/the-advisory-list)

## Install

```bash
pip install mcp-hangar
# or: uv pip install mcp-hangar
```

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

Or skip the config entirely -- get filesystem, fetch, and memory servers wired into Claude Desktop in one line:

```bash
curl -sSL https://mcp-hangar.io/install.sh | bash && mcp-hangar init -y && mcp-hangar serve
```

## What you get

- **Parallel tool calls** -- one `hangar_call` fans out to many MCP servers concurrently; all results returned together.
- **Lifecycle management** -- lazy start, health checks, single-flight cold starts, idle shutdown, and per-server circuit breaking.
- **Hot config reload** -- add or withdraw servers and tools via file watch, no restart.
- **Per-tenant tool projection** -- front-door mode presents a different executable surface per caller, fail-closed on unknown identity.
- **OAuth ingress** -- advertise as an RFC 9728 protected resource and challenge external agents for verified tokens.
- **Observability built in** -- OpenTelemetry traces, Prometheus metrics, structured logs, and an event-sourced audit trail.

## Documentation

- [Getting Started](https://mcp-hangar.io/docs/getting-started/quickstart) &middot; [Configuration](https://mcp-hangar.io/docs/reference/configuration) &middot; [Python API](https://mcp-hangar.io/docs/guides/FACADE_API)
- [Governance & Front Door](https://mcp-hangar.io/docs/guides/FRONT_DOOR) &middot; [Authentication & RBAC](https://mcp-hangar.io/docs/guides/AUTHENTICATION) &middot; [Observability](https://mcp-hangar.io/docs/guides/OBSERVABILITY)
- [Kubernetes operator](https://github.com/mcp-hangar/mcp-hangar-operator) &middot; [Helm charts](https://github.com/mcp-hangar/helm-charts) &middot; [All docs](https://mcp-hangar.io/docs)

## License

[MIT](LICENSE)
