# MCP Hangar + OpenTelemetry Collector

Quick start for exporting MCP governance telemetry to an OTEL Collector.

## What this example demonstrates

- Hangar emits OTLP traces (tool invocations, provider lifecycle) and audit logs (tool invocation events, state changes)
- OTEL Collector receives all telemetry on port 4317 (gRPC) or 4318 (HTTP)
- Prometheus scrapes governance metrics from the collector's `/metrics` endpoint
- Console exporter prints spans and audit logs to stdout for debugging

## Run

```bash
docker-compose up
```

Open Prometheus at http://localhost:9090. Query `mcp_hangar_tool_calls_total` to see tool invocation metrics.

## Key OTEL attributes

Governance spans and logs carry these MCP-specific attributes:

| Attribute | Description |
|-----------|-------------|
| `mcp.provider.id` | Provider identifier |
| `mcp.tool.name` | Tool name |
| `mcp.tool.status` | "success", "error", "timeout", "blocked" |
| `mcp.user.id` | Calling user identity (if propagated) |
| `mcp.session.id` | MCP session identifier |
| `mcp.enforcement.action` | Enforcement action taken |
| `mcp.enforcement.violation_type` | Type of capability violation |

See `src/mcp_hangar/observability/conventions.py` for the full attribute taxonomy.

## Integrate with OpenLIT

Replace the `logging` exporter in `otel-collector-config.yaml` with the OpenLIT OTLP endpoint. See `examples/openlit/` for a complete recipe.
