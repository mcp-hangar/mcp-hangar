### caller user, agent and session ids are off spans unless `observability.tracing.caller_ids` is on

Since 2.22.0 every `batch.call.<tool>` span carried `mcp.caller.id`,
`mcp.user.id`, `mcp.agent.id` and `mcp.session.id` for an authenticated caller.
They are now left off by default, as the telemetry data contract requires.
`mcp.caller.type`, `mcp.caller.tenant_id` and `mcp.correlation_id` are still set.
The attribute names are unchanged: only whether they are emitted changed.

A dashboard, alert or trace query that selects spans by `mcp.caller.id` or
`mcp.user.id` finds nothing after the upgrade. Either move it to OTLP audit
records, which carry caller identity whatever this setting says, or turn the ids
back on:

```yaml
observability:
  tracing:
    caller_ids: true
```

`MCP_TRACING_CALLER_IDS=true` does the same, and wins over the file.
