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

## What is in it

One provider: the official
[filesystem server](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem),
as the image Docker publishes for it. Nothing to build, no account, no
credentials -- the sandbox it is given is `/srv/hangar-quickstart/data` on the
host, created by the compose run.

The gateway starts it as a container on first use, which is why the compose
file mounts the Docker socket and adds the gateway to the socket's group.
**Access to the Docker socket is equivalent to root on the host**: it is here
because a container-mode provider needs it, and a subprocess or remote provider
would not. If your socket's group is not gid 999, run
`DOCKER_GID=$(stat -c %g /var/run/docker.sock) docker compose up -d`.

Call the provider through the gateway:

```bash
curl -s http://localhost:8080/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/call' \
  -H 'Mcp-Name: hangar_call' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
        "name":"hangar_call",
        "arguments":{"calls":[{"mcp_server":"filesystem","tool":"list_directory",
                               "arguments":{"path":"/data"}}]},
        "_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28",
                 "io.modelcontextprotocol/clientCapabilities":{}}}}'
```

Four things that are easy to get wrong and answer `400` rather than a wrong
result: `_meta` needs **both** envelope keys, `MCP-Protocol-Version` must equal
the one in `_meta`, `Mcp-Method` must equal the body's method, and `Mcp-Name`
must equal `params.name` (SEP-2243). `Accept` must also allow
`text/event-stream`.

Read the answer's `success` field, not just the HTTP status: a batch whose
calls failed still comes back `200` with `isError: false`.

`hangar_call` rather than the tool directly: in the default topology the
gateway serves its own `hangar_*` API and routes to providers through it. The
`front_door` topology projects each provider's tools under their own names
instead.

The first call is slow: it pulls the provider image and cold-starts it.

## Services

| Service | URL | Description |
|---------|-----|-------------|
| MCP-Hangar | http://localhost:8080 | Main MCP server |
| Prometheus | http://localhost:9090 | Metrics collection |
| Grafana | http://localhost:3000 | Dashboards (admin/admin) |

## Endpoints

- `GET /health/live` - liveness (is the process up?)
- `GET /health/ready` - readiness (can it serve?)
- `GET /health/startup` - startup (is initialization done?)
- `GET /metrics` - Prometheus metrics
- `POST /mcp` - MCP streamable HTTP endpoint

There is no bare `GET /health`; it answers 404.

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
