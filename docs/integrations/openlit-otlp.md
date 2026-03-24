# OpenLIT via OTLP

Short sketch for the MCP Hangar -> OpenTelemetry -> OpenLIT integration path.

## Goal

Use OpenLIT as a visibility layer for Hangar telemetry.

Hangar remains the runtime security and governance layer.
OpenLIT remains a partner backend for traces, metrics, and logs.

## Topology

```text
Agent / MCP client
        |
        v
   MCP Hangar
        |
        v
 OTLP / OTEL Collector
        |
        v
    OpenLIT
```

## Scope

- OTLP export first
- No native OpenLIT dependency in core
- No product pivot into generic AI observability
- Docs-first integration path

## What to send

- provider lifecycle events
- tool invocation traces
- health and state transitions
- audit signals
- policy and enforcement metadata
- user / session / trace context

## Key OTEL fields

- `provider`
- `tool`
- `group`
- `user`
- `session`
- `policy`
- `enforcement_action`
- `risk_signal`

## Minimal Hangar config

```yaml
observability:
  tracing:
    enabled: true
    otlp_endpoint: http://localhost:4317
    service_name: mcp-hangar
```

## Minimal OpenLIT path

- run OpenLIT
- point Hangar OTLP to OpenLIT or an OTEL Collector
- generate provider traffic
- inspect traces and governance signals in OpenLIT

## Expected outcome

OpenLIT shows Hangar as an OTEL-emitting service with MCP-aware governance telemetry.

## Later additions

- full config example
- OTEL Collector example
- attribute taxonomy table
- screenshots
- troubleshooting
- production deployment notes
