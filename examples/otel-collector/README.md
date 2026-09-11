# MCP Hangar + OpenTelemetry Collector

A small interoperability fixture: Hangar sends traces and audit logs to an
OpenTelemetry Collector over OTLP gRPC. CI
(`.github/workflows/examples-compose.yml`) makes one tool call and checks that
its span and its `tool_invocation` audit record reach the Collector.

## What is delivered

| Signal | How it gets there |
|--------|-------------------|
| Traces | OTLP gRPC from Hangar to the Collector |
| Audit logs (`tool_invocation`, `mcp_server_state_change`) | OTLP gRPC from Hangar to the Collector |
| Metrics | Not sent over OTLP. Prometheus scrapes Hangar's `/metrics` directly |

The Collector writes what it receives to stdout (`debug` exporter) and to
`/otel-output/telemetry.jsonl` (`file` exporter): OTLP JSON, one export
request per line.

`OTEL_EXPORTER_OTLP_ENDPOINT` is set explicitly, which turns on both trace and
audit log export. Its `http://` scheme selects plaintext gRPC. That suits a
Collector on the same Docker network, and nothing beyond it.

## Run

```bash
docker compose up
```

Hangar starts the `math` server (`examples/provider_math/`, mounted into the
container) on the first call:

```bash
curl -s http://localhost:8080/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/call' \
  -H 'Mcp-Name: hangar_call' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"hangar_call","arguments":{"calls":[{"mcp_server":"math","tool":"add","arguments":{"a":2,"b":3}}]},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
```

## Check what arrived

Copy the Collector's output out of its volume, then list the audit records and
the span names:

```bash
docker compose cp otel-collector:/otel-output/telemetry.jsonl .
jq -c '.resourceLogs[]?.scopeLogs[] | select(.scope.name == "mcp_hangar.audit")
  | .logRecords[] | [.attributes[] | {key, value: (.value | to_entries[0].value)}]
  | from_entries' telemetry.jsonl
jq -r '.resourceSpans[]?.scopeSpans[].spans[].name' telemetry.jsonl
```

To tag a run, set `HANGAR_RUN_ID` before `docker compose up`. Hangar puts it on
the resource of every span and audit record as `service.instance.id`
(default `local`).

For metrics, open Prometheus at `http://localhost:9090` and query
`mcp_hangar_tool_calls_total`.

## Key OTEL attributes

`tool_invocation` audit records carry:

| Attribute | Description |
|-----------|-------------|
| `mcp.event.name` | `tool_invocation` |
| `mcp.server.id` | MCP server identifier |
| `gen_ai.tool.name` | Tool name |
| `mcp.tool.status` | `success` or `error` |
| `mcp.tool.duration_ms` | Invocation duration |
| `mcp.caller.type`, `mcp.caller.id`, `mcp.caller.roles` | The caller, when the call carries an identity (not in this example, which runs without authentication) |

The spans of a call, such as `batch.call.<tool>` and `policy.check_access`,
carry `mcp.server.id` and `gen_ai.tool.name`. See
`src/mcp_hangar/observability/conventions.py` for the full attribute taxonomy.

## Integrate with OpenLIT

Replace the `debug` exporter in `otel-collector-config.yaml` with the OpenLIT
OTLP endpoint. See `examples/openlit/` for a complete recipe.
