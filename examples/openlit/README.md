# MCP Hangar + OpenLIT

Visualize MCP governance telemetry in OpenLIT.

## What you get

- Tool invocation traces in OpenLIT's trace explorer
- MCP Server lifecycle events (COLD -> READY -> DEGRADED) as audit log records
- Violation and enforcement signals searchable by `mcp.enforcement.violation_type`
- Filter all tool calls by user: `mcp.user.id = "alice"`

## Run

```sh
docker-compose up
```

Open OpenLIT at http://localhost:3000. Look in Traces for spans with `mcp.tool.name`.

## Key MCP governance attributes in OpenLIT

| Attribute | Example | Description |
|-----------|---------|-------------|
| `mcp.server.id` | `math-server` | MCP Server that handled the call |
| `mcp.tool.name` | `add` | Tool that was invoked |
| `mcp.tool.status` | `success` / `error` | Invocation outcome |
| `mcp.tool.duration_ms` | `12.5` | Call duration |
| `mcp.user.id` | `alice` | Calling user (if identity propagated) |
| `mcp.session.id` | `sess-abc` | MCP session |
| `mcp.enforcement.action` | `block` | Enforcement action taken |
| `mcp.enforcement.violation_type` | `egress_undeclared` | Capability violation type |

For the full attribute taxonomy, see `src/mcp_hangar/observability/conventions.py`.

## Integration stance

OpenLIT is a visibility partner for Hangar, not a replacement. Hangar focuses on
runtime security enforcement, lifecycle management, and governance. OpenLIT provides
the trace explorer, session analytics, and cost attribution UI. They work together
through the OpenTelemetry interoperability contract.
