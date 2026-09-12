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

Two containers: the gateway, and the official
[everything server](https://github.com/modelcontextprotocol/servers/tree/main/src/everything)
beside it, run from its npm package on a stock node image. Nothing to build, no
account, no credentials.

The gateway reaches it with `mode: remote` over the compose network. Two things
that pushed the example to this shape, both worth knowing before you write your
own config:

- **`mode: container` does not work from inside the Hangar image.** Container
  mode shells out to a `podman` or `docker` CLI on the host running Hangar, and
  the published image has neither. Mounting the Docker socket does not help --
  the socket is not what it uses. Hangar says so plainly when you try.
- **`everything` is the only official server that speaks HTTP**
  (`streamableHttp` is its transport argument). The rest are stdio-only, and a
  gateway in its own container cannot attach to another container's stdio
  without a bridge beside it.
- **The package, not the published image.** `mcp/everything` exists, but it was
  last rebuilt in 2025 and its build ignores the transport argument -- it comes
  up on stdio and the gateway gets `Connection refused`. The npm package is
  released continuously, which is why the docs recommend `npx -y
  @modelcontextprotocol/server-*` for anything you do not have to run as an
  image.

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
        "arguments":{"calls":[{"mcp_server":"everything","tool":"echo",
                               "arguments":{"message":"hello"}}]},
        "_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28",
                 "io.modelcontextprotocol/clientCapabilities":{}}}}'
```

Four things that answer `400` rather than a wrong result: `_meta` needs **both**
envelope keys, `MCP-Protocol-Version` must equal the one in `_meta`,
`Mcp-Method` must equal the body's method, and `Mcp-Name` must equal
`params.name` (SEP-2243). `Accept` must also allow `text/event-stream`.

Read the answer's `success` field, not just the HTTP status: a batch whose
calls failed still comes back `200` with `isError: false`.

`hangar_call` rather than the tool directly: in the default topology the
gateway serves its own `hangar_*` API and routes to providers through it. The
`front_door` topology projects each provider's tools under their own names
instead.

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

An OTLP endpoint set explicitly, in `OTEL_EXPORTER_OTLP_ENDPOINT` or in
`observability.tracing.otlp_endpoint`, also turns on OTLP export of audit
records, which carry caller identities. To export traces without them, set
`MCP_AUDIT_EXPORT_ENABLED=false`, or `observability.audit.enabled: false` in
`config.yaml`. The environment variable wins over the file, and the default is
`true`.

## Length limits

Hangar bounds how long a value it records can be. Each limit is a number of
characters, and an environment variable changes it. Where several variables
apply, the first one set wins:

| Value | Default | Variables, first set wins |
| --- | --- | --- |
| Span attribute | 256 | `OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT`, `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT`, `MCP_SPAN_ATTRIBUTE_LENGTH_LIMIT` |
| Span event or link attribute, such as an exception's message | 256 | `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT`, `MCP_SPAN_ATTRIBUTE_LENGTH_LIMIT` |
| OTLP audit record attribute | 256 | `OTEL_LOGRECORD_ATTRIBUTE_VALUE_LENGTH_LIMIT`, `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT`, `MCP_AUDIT_ATTRIBUTE_LENGTH_LIMIT` |
| String in a structured-log record | 2048 | `MCP_LOG_FIELD_LENGTH_LIMIT` |
| Free-text field of a domain event, such as `error_message` or `reasons` | 4096 | `MCP_EVENT_TEXT_LENGTH_LIMIT` |

A cut value ends with `…[truncated N]`, where N is the number of characters
removed, and the marker counts toward the limit. Span attributes are the
exception: the OpenTelemetry SDK cuts them and adds no marker. Log records are
cut after secrets are redacted. The span and audit limits apply only to the
tracer and logger providers Hangar builds. A provider registered before Hangar
starts keeps its own configuration.

## Cleanup

```bash
docker compose down -v
```

## Next Steps

- [Full Documentation](https://mcp-hangar.io)
- [Kubernetes Guide](https://mcp-hangar.io/guides/KUBERNETES/)
- [Container MCP servers](https://mcp-hangar.io/guides/CONTAINERS/)
