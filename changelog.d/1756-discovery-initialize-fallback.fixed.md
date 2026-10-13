**core:** discovery no longer quarantines a server only because it has no
health endpoint. When the health path (`mcp-hangar.io/health-path`, default
`/health`) answers 404, the security check sends an MCP `initialize` to the
server's MCP path (`mcp-hangar.io/path`, default `/mcp`) and passes it on a
JSON-RPC result, JSON or SSE. A 5xx or a timeout on the health path still fails
the check, and a failed fallback names both probes. Servers that serve only MCP,
including the Kubernetes operator's container-mode servers, no longer need
`discovery.security.require_health_check: false`.
