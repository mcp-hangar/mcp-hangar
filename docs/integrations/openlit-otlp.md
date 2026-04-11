# OpenLIT via OTLP

!!! note "This page has moved"
    The full OpenLIT integration guide, including docker-compose recipes, MCP
    attribute taxonomy, and getting-started instructions, is now part of the
    unified OpenTelemetry integrations page:
    **[OpenTelemetry Integrations -- OpenLIT section](../observability/otel-integrations.md#openlit)**

## Quick reference

- **Example:** [`examples/openlit/`](https://github.com/mcp-hangar/mcp-hangar/tree/main/examples/openlit)
- **Topology:** Agent -> Hangar (OTLP) -> OTEL Collector -> OpenLIT
- **Key env var:** `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317`

## Integration stance

Hangar exports governance telemetry via OTEL. OpenLIT visualizes it.
Hangar does not try to become a generic AI observability platform.
OpenLIT does not replace Hangar's runtime security enforcement.

See [ROADMAP.md](https://github.com/mcp-hangar/mcp-hangar/blob/main/ROADMAP.md)
for the full integration philosophy.
