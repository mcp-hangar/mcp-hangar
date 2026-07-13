# MCP-Hangar Quickstart

Get MCP-Hangar running in seconds with Docker Compose.

## Requirements

- Docker
- Docker Compose

## Quick Start

```bash
# Start all services
docker compose up -d

# Check status
docker compose ps

# View logs
docker compose logs -f mcp-hangar
```

## Services

| Service | URL | Description |
|---------|-----|-------------|
| MCP-Hangar | http://localhost:8080 | Main MCP server |
| Prometheus | http://localhost:9090 | Metrics collection |
| Grafana | http://localhost:3000 | Dashboards (admin/admin) |

## Endpoints

- `GET /health` - Health check
- `GET /metrics` - Prometheus metrics
- `POST /mcp/v1/...` - MCP protocol

## Configuration

Edit `config.yaml` to add MCP servers:

```yaml
mcp_servers:
  math:
    mode: subprocess
    command: ["python", "-m", "math_server"]
```

Then restart:

```bash
docker compose restart mcp-hangar
```

## Tracing

Distributed tracing is enabled by default and exports spans over OTLP/gRPC to
`http://localhost:4317`. If no collector is listening there, export runs on a
background thread and never blocks the MCP path, but the OTLP exporter logs
periodic `Failed to export traces ... UNAVAILABLE` / `Transient error ... retrying`
warnings. Failed export batches (and the spans dropped with them) are counted in
the `mcp_hangar_otlp_export_failures_total` metric on `/metrics`.

To silence that noise when running locally without a collector, disable tracing:

```bash
MCP_TRACING_ENABLED=false docker compose up -d
```

Or point it at a real collector via `OTEL_EXPORTER_OTLP_ENDPOINT` (see
`examples/otel-collector/`).

## Cleanup

```bash
docker compose down -v
```

## Next Steps

- [Full Documentation](https://mcp-hangar.io)
- [Kubernetes Guide](https://mcp-hangar.io/guides/KUBERNETES/)
- [Container MCP servers](https://mcp-hangar.io/guides/CONTAINERS/)
