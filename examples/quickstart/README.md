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

Edit `config.yaml` to add providers:

```yaml
providers:
  math:
    mode: subprocess
    command: ["python", "-m", "math_server"]
```

Then restart:

```bash
docker compose restart mcp-hangar
```

## Cleanup

```bash
docker compose down -v
```

## Next Steps

- [Full Documentation](https://mapyr.github.io/mcp-hangar/)
- [Kubernetes Guide](https://mapyr.github.io/mcp-hangar/guides/KUBERNETES/)
- [Container Providers](https://mapyr.github.io/mcp-hangar/guides/CONTAINERS/)
