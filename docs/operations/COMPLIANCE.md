# Compliance Export

Hangar can forward audit events to a SIEM or log aggregator in a structured
format. Four formats are supported: CEF, LEEF 2.0, JSON-lines, and RFC 5424
syslog.

## Configuration

Two environment variables control the compliance pipeline:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MCP_COMPLIANCE_FORMAT` | Yes | _(unset)_ | Format to use: `cef`, `leef`, `jsonlines`, `json-lines`, `syslog`. Case-insensitive. |
| `MCP_COMPLIANCE_OUTPUT` | No | stderr | File path to write output. When unset, lines go to stderr for container log collection. |

When `MCP_COMPLIANCE_FORMAT` is set, Hangar registers a second audit event
handler that forwards `ToolInvocationCompleted`, `ToolInvocationFailed`, and
`McpServerStateChanged` events to the chosen exporter. This handler runs
independently of the OTLP audit exporter.

If the enterprise module is not installed, Hangar logs a warning and continues
without the compliance handler.

## Format reference

### CEF (Common Event Format)

```
CEF:0|MCP Hangar|MCP Hangar|0.15.0|100|ToolInvocationCompleted|5|...extensions...
```

Compatible with ArcSight, Splunk, QRadar, and any CEF-aware SIEM.

### LEEF 2.0 (IBM QRadar)

```
LEEF:2.0|MCP Hangar|MCP Hangar|0.15.0|101|\tproto=tool\taction=add\t...
```

Tab-delimited extensions following the LEEF 2.0 specification.

### JSON-lines

```json
{"timestamp":"2026-05-10T12:00:00+00:00","event_type":"ToolInvocationCompleted","provider_id":"math","tool_name":"add",...}
```

One JSON object per line. Fields include `event_type`, `provider_id`
(legacy name for `mcp_server_id`), `tool_name`, `status`, `duration_ms`,
and optional `caller_*` / `cost_*` fields.

### RFC 5424 syslog

```
<134>1 2026-05-10T12:00:00+00:00 mcp-hangar mcp-hangar - - - ToolInvocationCompleted ...
```

Structured data follows RFC 5424. Suitable for rsyslog, syslog-ng, and
Fluentd syslog inputs.

## Examples

Start Hangar with CEF output to a file:

```bash
MCP_COMPLIANCE_FORMAT=cef MCP_COMPLIANCE_OUTPUT=/var/log/mcp-hangar/cef.log \
  mcp-hangar serve --http --port 8000
```

JSON-lines to stderr (for Docker log drivers):

```bash
MCP_COMPLIANCE_FORMAT=jsonlines mcp-hangar serve --http --port 8000
```

## Exported fields

Each tool invocation record includes:

| Field | Source | Notes |
|-------|--------|-------|
| `mcp_server_id` | Event | MCP server that handled the call |
| `tool_name` | Event | Tool name |
| `status` | Event | `success` or `error` |
| `duration_ms` | Event | Call duration |
| `caller_type` | Identity context | `human`, `agent`, `service`, `anonymous` |
| `caller_id` | Identity context | Principal identifier |
| `caller_roles` | Identity context | Comma-separated roles |
| `cost_cents` | Cost attributor | Cost in hundredths of a cent (when configured) |
| `cost_model` | Cost attributor | Pricing model: `token`, `duration`, `fixed`, `composite` |
| `cost_input_tokens` | Cost attributor | Input tokens consumed |
| `cost_output_tokens` | Cost attributor | Output tokens produced |
