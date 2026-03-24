# OpenTelemetry Integrations

Hangar is the **runtime governance layer** for MCP servers. It is not an observability
platform. Hangar exports governance telemetry -- enforcement decisions, provider
lifecycle events, capability violations, and identity-aware audit trails -- through
the OpenTelemetry (OTEL) interoperability contract. Partner backends visualize it.

```
Agent --> Hangar (governance) --> OTLP --> [OTEL Collector | OpenLIT | Langfuse | Grafana]
```

This page covers how to connect Hangar to each supported backend and what
governance data flows through.

---

## MCP Attribute Taxonomy

Every span, metric, and audit log emitted by Hangar carries MCP-specific attributes
defined in `src/mcp_hangar/observability/conventions.py`. These attributes form a
stable contract that partner backends consume without Hangar-specific plugins.

### Provider attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `mcp.provider.id` | string | Unique provider identifier (e.g. `math-server`) |
| `mcp.provider.mode` | string | Operational mode: `subprocess`, `docker`, `remote` |
| `mcp.provider.state` | string | Lifecycle state: `COLD`, `INITIALIZING`, `READY`, `DEGRADED`, `DEAD` |
| `mcp.provider.group_id` | string | Provider group membership |
| `mcp.provider.image` | string | Container image reference (docker mode) |
| `mcp.provider.has_capabilities` | string | Whether provider declares capabilities (`true`/`false`) |
| `mcp.provider.enforcement_mode` | string | Declared enforcement mode: `alert`, `block`, `quarantine` |

### Tool invocation attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `mcp.tool.name` | string | Tool name as advertised by the provider |
| `mcp.tool.duration_ms` | float | Call duration in milliseconds |
| `mcp.tool.status` | string | Result: `success`, `error`, `timeout`, `blocked` |
| `mcp.tool.cold_start` | string | Whether this call triggered a cold start (`true`/`false`) |
| `mcp.tool.args_hash` | string | Argument hash for audit (raw arguments are never exported) |
| `mcp.tool.response_tokens` | int | Approximate token count of tool response |
| `mcp.session.id` | string | MCP protocol session identifier |
| `mcp.agent.id` | string | Agent or client identifier |
| `mcp.user.id` | string | Human user identity behind the agent request |
| `mcp.correlation_id` | string | Correlation ID for multi-step agent workflows |

### Enforcement attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `mcp.enforcement.policy_result` | string | Policy evaluation result: `allow`, `deny`, `quarantine` |
| `mcp.enforcement.policy_name` | string | Name of the evaluated policy |
| `mcp.enforcement.action` | string | Action taken: `none`, `alert`, `block`, `quarantine`, `rate_limit` |
| `mcp.enforcement.violation_type` | string | Violation category: `egress_undeclared`, `tool_schema_drift`, `resource_limit_exceeded` |
| `mcp.enforcement.egress_destination` | string | Destination involved in egress violation (host:port) |
| `mcp.enforcement.violation_count` | int | Accumulated violations for this provider in this session |

### Audit attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `mcp.audit.principal_type` | string | Principal type: `api_key`, `jwt`, `oidc`, `anonymous` |
| `mcp.audit.principal_id` | string | Principal identifier (API key ID, JWT sub claim) |
| `mcp.audit.principal_roles` | string | Roles held at call time (comma-separated) |
| `mcp.audit.authenticated` | string | Whether request passed authentication (`true`/`false`) |
| `mcp.audit.authorized` | string | Whether request passed authorization (`true`/`false`) |
| `mcp.audit.data_sensitivity` | string | Response classification: `public`, `internal`, `confidential`, `restricted` |

### Behavioral attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `mcp.behavioral.matches_baseline` | string | Whether call matches baseline pattern (`true`/`false`) |
| `mcp.behavioral.anomaly_score` | float | Anomaly score (0.0 = normal, 1.0 = highly anomalous) |
| `mcp.behavioral.rule_id` | string | Detection rule that matched |
| `mcp.behavioral.pattern_step` | int | Sequence position in a detected multi-step pattern |
| `mcp.behavioral.pattern_name` | string | Name of the detected behavioral pattern |

### Health attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `mcp.health.result` | string | Health check result: `passed`, `failed`, `timeout` |
| `mcp.health.consecutive_failures` | int | Number of consecutive failures |
| `mcp.health.duration_ms` | float | Health check response time in milliseconds |

### Prometheus metric names

| Metric | Type | Description |
|--------|------|-------------|
| `mcp_hangar_tool_calls_total` | Counter | Total tool invocations |
| `mcp_hangar_tool_call_duration_seconds` | Histogram | Tool call latency distribution |
| `mcp_hangar_provider_state` | Gauge | Current provider lifecycle state |
| `mcp_hangar_cold_starts_total` | Counter | Total cold starts |
| `mcp_hangar_health_checks_total` | Counter | Total health checks |
| `mcp_hangar_circuit_breaker_state` | Gauge | Circuit breaker state per provider |
| `mcp_hangar_capability_violations_total` | Counter | Total capability violations |
| `mcp_hangar_egress_blocked_total` | Counter | Total blocked egress attempts |
| `mcp_hangar_providers_quarantined` | Gauge | Providers currently quarantined |
| `mcp_hangar_tool_schema_drifts_total` | Counter | Total tool schema drift detections |

---

## OTEL Collector

The OTEL Collector is the recommended entry point for governance telemetry. It
receives OTLP from Hangar and routes spans, metrics, and logs to any supported
backend -- Prometheus, Jaeger, OpenLIT, Datadog, or a custom pipeline.

**Example:** [`examples/otel-collector/`](https://github.com/mcp-hangar/mcp-hangar/tree/main/examples/otel-collector)

### Getting started

Set these environment variables before starting Hangar:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_SERVICE_NAME=mcp-hangar
MCP_TRACING_ENABLED=true
```

Start the reference stack:

```bash
cd examples/otel-collector
docker-compose up
```

This starts:

| Service | Port | Purpose |
|---------|------|---------|
| Hangar | 8080 | MCP control plane with OTLP export |
| OTEL Collector | 4317 (gRPC), 4318 (HTTP) | Telemetry receiver and router |
| Prometheus | 9090 | Metrics storage and query |

The collector config (`otel-collector-config.yaml`) receives OTLP on both gRPC
and HTTP, exports metrics to Prometheus, and prints spans and logs to console for
debugging. Replace the `logging` exporter with your production backend.

### What flows through the collector

- **Traces:** Tool invocation spans carrying `mcp.provider.id`, `mcp.tool.name`,
  `mcp.tool.status`, and enforcement attributes.
- **Logs:** Audit log records for tool invocation events and provider state
  transitions, exported by `OTLPAuditExporter`.
- **Metrics:** Prometheus metrics scraped from Hangar's `/metrics` endpoint or
  forwarded through the collector's Prometheus exporter.

---

## OpenLIT

OpenLIT provides a trace explorer, session analytics, and cost attribution UI.
Hangar exports governance telemetry; OpenLIT visualizes it. They connect through
the OTEL Collector -- Hangar does not send data directly to OpenLIT.

**Example:** [`examples/openlit/`](https://github.com/mcp-hangar/mcp-hangar/tree/main/examples/openlit)

### Getting started

```bash
cd examples/openlit
docker-compose up
```

This starts Hangar, an OTEL Collector, and OpenLIT. Open the OpenLIT dashboard at
<http://localhost:3000>.

Environment variables for Hangar (already set in the docker-compose):

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_SERVICE_NAME=mcp-hangar
MCP_TRACING_ENABLED=true
```

### What governance data is visible in OpenLIT

In the OpenLIT trace explorer, filter on MCP governance attributes:

- **By provider:** `mcp.provider.id = "math-server"`
- **By tool:** `mcp.tool.name = "add"`
- **By user:** `mcp.user.id = "alice"`
- **By enforcement action:** `mcp.enforcement.action = "block"`
- **By violation type:** `mcp.enforcement.violation_type = "egress_undeclared"`

Provider lifecycle events (COLD, INITIALIZING, READY, DEGRADED, DEAD) appear as
audit log records with `mcp.provider.state` attributes.

---

## Langfuse

Langfuse provides LLM-specific observability: input/output recording, token
counting, user session tracking, and evaluation workflows. It complements the
OTEL governance telemetry path -- Langfuse handles LLM observability while OTEL
handles governance observability.

- **OTEL path:** Enforcement decisions, capability violations, provider lifecycle,
  audit trails. Exported via OTLP to any OTEL-compatible backend.
- **Langfuse path:** Tool call input/output, token counts, user session traces.
  Exported via the `LangfuseObservabilityAdapter`.

**Example:** [`examples/langfuse/`](https://github.com/mcp-hangar/mcp-hangar/tree/main/examples/langfuse)

### Getting started

You need a running Langfuse instance -- either [Langfuse Cloud](https://cloud.langfuse.com/)
or a self-hosted deployment.

Set these environment variables:

```bash
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com   # or your self-hosted URL
```

Enable Langfuse in `config.yaml`:

```yaml
observability:
  langfuse:
    enabled: true
    # Keys are read from environment variables
    # Never put secret keys in config files
```

!!! warning "Secret handling"
    `LANGFUSE_SECRET_KEY` is a secret. Use environment variables, HashiCorp Vault,
    or Kubernetes secrets. Never commit secrets to config files or source control.

### How Hangar maps to Langfuse concepts

| Langfuse concept | Hangar mapping |
|------------------|----------------|
| Trace | One MCP session (`mcp.session.id`) |
| Span | Provider tool invocation |
| Generation | Tool call with input/output |
| User | `mcp.user.id` from identity propagation |

When Hangar propagates caller identity, Langfuse traces carry the same `user_id`
and `session_id` as the MCP OTEL spans. This enables cross-referencing Langfuse
traces with governance enforcement events in OTEL backends.

---

## Grafana

Hangar exposes Prometheus metrics at the `/metrics` HTTP endpoint. Grafana can
scrape these directly or consume them through the OTEL Collector's Prometheus
exporter.

Pre-built Grafana dashboards are available in the [`monitoring/`](https://github.com/mcp-hangar/mcp-hangar/tree/main/monitoring)
directory:

- **Overview dashboard:** Provider states, tool call rates, error rates
- **Provider details dashboard:** Per-provider metrics, health check history
- **Alerts dashboard:** Circuit breaker state, violation counts

### Getting started

Start the monitoring stack:

```bash
cd monitoring
docker compose up -d
```

| Service | URL | Credentials |
|---------|-----|-------------|
| Grafana | <http://localhost:3000> | admin / admin |
| Prometheus | <http://localhost:9090> | -- |
| Alertmanager | <http://localhost:9093> | -- |

Start Hangar in HTTP mode so the `/metrics` endpoint is available:

```bash
mcp-hangar serve --http --port 8000
```

### Key Prometheus queries

```promql
# Tool call rate per provider (last 5 minutes)
rate(mcp_hangar_tool_calls_total[5m])

# 95th percentile tool call latency
histogram_quantile(0.95, rate(mcp_hangar_tool_call_duration_seconds_bucket[5m]))

# Providers currently in DEGRADED state
mcp_hangar_provider_state{state="DEGRADED"} == 1

# Circuit breaker open count
mcp_hangar_circuit_breaker_state == 1
```

---

## Integration stance

Hangar is not trying to become an observability platform. OpenTelemetry-compatible
tools -- OpenLIT, Langfuse, Grafana, OTEL Collector, Datadog, Honeycomb -- are
**extensions to the visibility layer** around Hangar. Hangar defines the governance
telemetry contract (the MCP attribute taxonomy above) and exports it via OTEL.
Partner tools consume, correlate, alert, and visualize.

This separation means:

- Hangar focuses on runtime security enforcement, lifecycle management, and
  governance policy.
- Partner tools focus on visualization, alerting, session analytics, and cost
  attribution.
- The OTEL interoperability contract ensures any OTEL-compatible tool works
  without Hangar-specific plugins.
