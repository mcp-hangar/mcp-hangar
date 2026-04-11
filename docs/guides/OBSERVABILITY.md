# Observability Guide

This guide covers MCP Hangar's observability features: metrics, tracing, logging, and health checks.

## Table of Contents

- [Quick Start](#quick-start)
- [Monitoring Stack](#monitoring-stack)
- [Metrics](#metrics)
- [Grafana Dashboards](#grafana-dashboards)
- [Alerting](#alerting)
- [Tracing](#tracing)
- [Langfuse Integration](#langfuse-integration)
- [Logging](#logging)
- [Health Checks](#health-checks)
- [SLIs/SLOs](#slisslos)
- [Troubleshooting](#troubleshooting)
- [Best Practices](#best-practices)

## Quick Start

### Prerequisites

```bash
# Core package
pip install mcp-hangar

# For full observability support
pip install mcp-hangar[observability]
```

### Start Monitoring Stack

The monitoring stack is in `monitoring/` and includes Prometheus, Grafana, and Alertmanager:

```bash
# Using Docker Compose
cd monitoring
docker compose up -d

# Using Podman
cd monitoring
podman compose up -d
```

Access dashboards:

| Service | URL | Credentials |
|---------|-----|-------------|
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus | http://localhost:9090 | - |
| Alertmanager | http://localhost:9093 | - |

### Start MCP Hangar with Metrics

```bash
# HTTP mode (exposes /metrics endpoint)
mcp-hangar serve --http --port 8000

# With custom config
MCP_CONFIG=config.yaml mcp-hangar serve --http --port 8000
```

Verify metrics are exposed:

```bash
curl http://localhost:8000/metrics | grep mcp_hangar
```

## Monitoring Stack

### Architecture

```
+----------------+     scrape      +------------+
|  MCP Hangar    |---------------->| Prometheus |
|  :8000/metrics |                 |   :9090    |
+----------------+                 +-----+------+
                                         |
                                         | query
                                         v
                                   +------------+
                                   |  Grafana   |
                                   |   :3000    |
                                   +------------+

+----------------+     alerts      +-------------+
|  Prometheus    |---------------->| Alertmanager|
|  alert rules   |                 |    :9093    |
+----------------+                 +-------------+
```

### Configuration Files

| File | Purpose |
|------|---------|
| `monitoring/docker-compose.yaml` | Container orchestration |
| `monitoring/prometheus/prometheus.yaml` | Scrape configuration |
| `monitoring/prometheus/alerts.yaml` | Alert rules |
| `monitoring/alertmanager/alertmanager.yaml` | Notification routing |
| `monitoring/grafana/provisioning/` | Dashboard/datasource provisioning |
| `monitoring/grafana/dashboards/` | Pre-built dashboard JSON files |

### Prometheus Configuration

The default configuration scrapes MCP Hangar every 10 seconds:

```yaml
# monitoring/prometheus/prometheus.yaml
scrape_configs:
  - job_name: 'mcp-hangar'
    static_configs:
      - targets: ['host.docker.internal:8000']
        labels:
          service: 'mcp-hangar'
          tier: 'application'
    metrics_path: /metrics
    scrape_interval: 10s
    scrape_timeout: 5s
```

For Kubernetes deployments, use service discovery:

```yaml
scrape_configs:
  - job_name: 'mcp-hangar'
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app]
        regex: mcp-hangar
        action: keep
```

## Metrics

MCP Hangar exports Prometheus metrics at `/metrics`. All metrics use the `mcp_hangar_` prefix.

### Currently Exported Metrics

#### Tool Invocations

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mcp_hangar_tool_calls_total` | Counter | provider, tool, status | Total tool invocations |
| `mcp_hangar_tool_call_duration_seconds` | Histogram | provider, tool | Invocation latency (buckets: 0.01-30s) |
| `mcp_hangar_tool_call_errors_total` | Counter | provider, tool, error_type | Failed invocations by error type |

**Example queries:**

```promql
# Tool call rate by provider
sum(rate(mcp_hangar_tool_calls_total[5m])) by (provider)

# P95 latency by tool
histogram_quantile(0.95, sum(rate(mcp_hangar_tool_call_duration_seconds_bucket[5m])) by (le, tool))

# Error rate
sum(rate(mcp_hangar_tool_call_errors_total[5m])) / sum(rate(mcp_hangar_tool_calls_total[5m]))
```

#### Batch Invocations

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mcp_hangar_batch_calls_total` | Counter | result | Batch invocations (success/failure) |
| `mcp_hangar_batch_duration_seconds` | Histogram | - | Batch execution time |
| `mcp_hangar_batch_size` | Histogram | - | Number of calls per batch |
| `mcp_hangar_batch_cancellations_total` | Counter | - | Cancelled batches |
| `mcp_hangar_batch_circuit_breaker_rejections_total` | Counter | - | Circuit breaker rejections |
| `mcp_hangar_batch_concurrency` | Gauge | - | Current parallel executions |

**Example queries:**

```promql
# Batch success rate
sum(rate(mcp_hangar_batch_calls_total{result="success"}[5m]))
/ sum(rate(mcp_hangar_batch_calls_total[5m]))

# Average batch size
rate(mcp_hangar_batch_size_sum[5m]) / rate(mcp_hangar_batch_size_count[5m])
```

#### Health Checks

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mcp_hangar_health_checks_total` | Counter | provider, result | Health check executions |
| `mcp_hangar_health_check_duration_seconds` | Histogram | provider | Health check latency |
| `mcp_hangar_health_check_consecutive_failures` | Gauge | provider | Current consecutive failure count |

**Example queries:**

```promql
# Unhealthy providers (>2 consecutive failures)
mcp_hangar_health_check_consecutive_failures > 2

# Health check success rate
sum(rate(mcp_hangar_health_checks_total{result="healthy"}[5m])) by (provider)
/ sum(rate(mcp_hangar_health_checks_total[5m])) by (provider)
```

#### Provider Lifecycle

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mcp_hangar_provider_starts_total` | Counter | provider | Provider start attempts |
| `mcp_hangar_provider_initialized` | Gauge | provider | 1 if provider has been initialized |

#### GC (Garbage Collection)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mcp_hangar_gc_cycles_total` | Counter | - | GC cycle executions |
| `mcp_hangar_gc_cycle_duration_seconds` | Histogram | - | GC cycle duration |

### Metrics Not Yet Implemented

The following metrics are defined in code but not currently populated. They are planned for future releases:

- `mcp_hangar_provider_state` - Provider state gauge (cold/ready/degraded/dead)
- `mcp_hangar_provider_up` - Provider availability
- `mcp_hangar_provider_cold_start_seconds` - Cold start latency histogram
- `mcp_hangar_discovery_*` - Auto-discovery metrics
- `mcp_hangar_http_*` - HTTP transport metrics (for remote providers)
- `mcp_hangar_rate_limit_hits_total` - Rate limiting metrics
- `mcp_hangar_connections_*` - Connection tracking

## Grafana Dashboards

Pre-built dashboards are provisioned automatically from `monitoring/grafana/dashboards/`:

### Overview Dashboard

**File:** `overview.json`
**URL:** http://localhost:3000/d/mcp-hangar-overview

Provides high-level system health:

- Request rate and error rate trends
- Latency percentiles (P50, P95, P99)
- Provider health status
- Batch invocation success/failure rates
- Health check results
- GC cycle performance

### Provider Details Dashboard

**File:** `provider-details.json`
**URL:** http://localhost:3000/d/mcp-hangar-provider-details

Deep dive into individual providers:

- Tool call breakdown by tool name
- Per-tool latency histograms
- Error distribution by type
- Health check history
- Consecutive failure tracking

### Alerts Dashboard

**File:** `alerts.json`
**URL:** http://localhost:3000/d/mcp-hangar-alerts

Alert monitoring and trends:

- Active alerts by severity
- Alert condition trends (error rate, latency, health)
- Historical alert timeline

### Importing Dashboards Manually

If not using provisioning:

1. Open Grafana at http://localhost:3000
2. Go to Dashboards > Import
3. Upload JSON file from `monitoring/grafana/dashboards/`
4. Select Prometheus data source
5. Click Import

## Alerting

### Alert Configuration

Alert rules are defined in `monitoring/prometheus/alerts.yaml` and organized by severity:

#### Critical Alerts (Page On-Call)

| Alert | Condition | For | Description |
|-------|-----------|-----|-------------|
| `MCPHangarNotResponding` | `up{job="mcp-hangar"} == 0` | 1m | Service unreachable |
| `MCPHangarHighErrorRate` | Error rate > 10% | 2m | Significant failures |
| `MCPHangarBatchHighFailureRate` | Batch failure > 20% | 3m | Batch operations failing |
| `MCPHangarCircuitBreakerTripped` | CB rejections > 10/5m | 2m | Provider isolated |
| `MCPHangarProviderUnhealthy` | Consecutive failures > 5 | 2m | Provider critically unhealthy |

#### Warning Alerts (Investigate)

| Alert | Condition | For | Description |
|-------|-----------|-----|-------------|
| `MCPHangarHighConsecutiveFailures` | Consecutive failures > 2 | 2m | Health check issues |
| `MCPHangarHealthCheckSlow` | P95 health check > 5s | 5m | Slow health checks |
| `MCPHangarHighLatencyP95` | P95 latency > 3s | 5m | Performance degradation |
| `MCPHangarHighLatencyP99` | P99 latency > 5s | 5m | Tail latency issues |
| `MCPHangarHighLatencyByTool` | P95 per-tool > 5s | 5m | Specific tool slow |
| `MCPHangarFrequentColdStarts` | Start rate > 0.1/s | 10m | Consider increasing idle_ttl |
| `MCPHangarBatchSlowExecution` | P95 batch > 30s | 5m | Slow batch processing |
| `MCPHangarBatchHighCancellationRate` | Cancellation > 10% | 5m | Batches timing out |
| `MCPHangarBatchSizeTooLarge` | P95 size > 50 | 5m | Consider smaller batches |
| `MCPHangarGCSlowCycles` | P95 GC > 0.5s | 5m | GC performance issue |
| `MCPHangarHighMemoryUsage` | Memory > 2GB | 10m | Memory pressure |
| `MCPHangarHighCPUUsage` | CPU > 80% | 10m | CPU saturation |

#### Info Alerts (Tracking)

| Alert | Condition | Description |
|-------|-----------|-------------|
| `MCPHangarProviderStarted` | Any provider start | Provider lifecycle event |
| `MCPHangarHighToolCallVolume` | Rate > 100/s | High traffic notification |

### Alertmanager Configuration

Configure notification routing in `monitoring/alertmanager/alertmanager.yaml`:

```yaml
route:
  group_by: ['alertname', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: 'default'
  routes:
    - match:
        severity: critical
      receiver: 'pagerduty'
    - match:
        severity: warning
      receiver: 'slack'

receivers:
  - name: 'default'
    webhook_configs:
      - url: 'http://your-webhook-endpoint'

  - name: 'pagerduty'
    pagerduty_configs:
      - service_key: '<your-service-key>'

  - name: 'slack'
    slack_configs:
      - api_url: '<your-slack-webhook-url>'
        channel: '#mcp-hangar-alerts'
        title: '{{ .CommonAnnotations.summary }}'
        text: '{{ .CommonAnnotations.description }}'
```

### Testing Alerts

Verify alert rules are loaded:

```bash
# Check Prometheus rules
curl -s http://localhost:9090/api/v1/rules | jq '.data.groups[].rules[].name'

# Check for firing alerts
curl -s http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.state=="firing")'
```

## Tracing

### OpenTelemetry Integration

MCP Hangar supports distributed tracing via OpenTelemetry. Every tool invocation
produces an OTEL span carrying MCP governance attributes (`mcp.provider.id`,
`mcp.tool.name`, `mcp.tool.status`, enforcement context, and identity context
when available).

For the full MCP attribute taxonomy, partner backend recipes (OTEL Collector,
OpenLIT, Langfuse, Grafana), and reference docker-compose setups, see:
**[OpenTelemetry Integrations](../observability/otel-integrations.md)**.

```python
from mcp_hangar.observability import init_tracing, trace_span

# Initialize once at startup
init_tracing(
    service_name="mcp-hangar",
    otlp_endpoint="http://localhost:4317",
)

# Create spans for operations
with trace_span("process_request", {"request.id": req_id}) as span:
    span.add_event("checkpoint_reached")
    result = do_work()
```

### MCP Governance Attributes on Spans

`TracedProviderService` automatically creates an OTEL span for each tool invocation
with standard MCP governance attributes via `set_governance_attributes()`:

```python
from mcp_hangar.observability.conventions import Provider, MCP, set_governance_attributes

# set_governance_attributes(span, ...) sets all applicable attributes in one call.
# None values are omitted -- no empty strings pollute OTLP backends.
set_governance_attributes(
    span,
    provider_id="math",
    tool_name="add",
    user_id="alice",           # optional
    session_id="sess-42",      # optional
    policy_result="allow",     # optional
    enforcement_action=None,   # omitted from span
)
```

### OTLP Audit Export

Security-relevant domain events (tool invocations, provider state transitions) are
automatically exported as OTLP log records when `OTEL_EXPORTER_OTLP_ENDPOINT` is
set. This is handled by `OTLPAuditExporter` and `OTLPAuditEventHandler` -- no
additional configuration needed.

Events exported:

- `ToolInvocationCompleted` / `ToolInvocationFailed` -- with provider, tool, status, duration
- `ProviderStateChanged` -- with provider, from_state, to_state

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_TRACING_ENABLED` | `true` | Enable/disable tracing |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP collector endpoint (also activates OTLP audit export) |
| `OTEL_SERVICE_NAME` | `mcp-hangar` | Service name in traces |

### Trace Context Propagation

W3C TraceContext is automatically propagated across agent -> Hangar -> provider
boundaries:

- **Inbound:** `BatchExecutor` extracts `traceparent` from call metadata, creating
  child spans linked to the agent's root trace.
- **Outbound:** `HttpClient` injects `traceparent` into outbound HTTP headers when
  calling remote providers.
- **Stdio:** Not supported (JSON-RPC over stdin/stdout has no header mechanism).

Manual propagation is also available:

```python
from mcp_hangar.observability import inject_trace_context, extract_trace_context

# Inject into outgoing requests
headers = {}
inject_trace_context(headers)

# Extract from incoming requests
context = extract_trace_context(request_headers)
```

## Langfuse Integration

MCP Hangar integrates with [Langfuse](https://langfuse.com) for LLM-specific observability.

### Configuration

```bash
export MCP_LANGFUSE_ENABLED=true
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
export LANGFUSE_HOST=https://cloud.langfuse.com
```

Or via config.yaml:

```yaml
observability:
  langfuse:
    enabled: true
    public_key: ${LANGFUSE_PUBLIC_KEY}
    secret_key: ${LANGFUSE_SECRET_KEY}
    host: https://cloud.langfuse.com
    sample_rate: 1.0
```

### Trace Propagation

```python
from mcp_hangar.application.services import TracedProviderService

result = traced_service.invoke_tool(
    provider_id="math",
    tool_name="add",
    arguments={"a": 1, "b": 2},
    trace_id="your-langfuse-trace-id",
    user_id="user-123",
    session_id="session-456",
)
```

See [ADR-001](../adr/001-langfuse-integration.md) for architectural details.

## Logging

### Structured Logging

MCP Hangar uses structlog for structured JSON logging:

```json
{
  "timestamp": "2026-02-03T10:30:00.123Z",
  "level": "info",
  "event": "tool_invoked",
  "provider": "math",
  "tool": "add",
  "duration_ms": 150,
  "service": "mcp-hangar"
}
```

### Configuration

```yaml
logging:
  level: INFO          # DEBUG, INFO, WARNING, ERROR
  json_format: true    # JSON output for log aggregation
```

Environment variable:

```bash
MCP_LOG_LEVEL=DEBUG mcp-hangar serve --http
```

### Log Correlation

Include trace IDs for correlation with distributed traces:

```python
from mcp_hangar.observability import get_current_trace_id
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)
logger.info("processing", trace_id=get_current_trace_id())
```

## Health Checks

### HTTP Endpoints

| Endpoint | Purpose | Use Case |
|----------|---------|----------|
| `/health/live` | Liveness | Container restart decisions |
| `/health/ready` | Readiness | Traffic routing |
| `/health/startup` | Startup | Initial boot gate |

### Response Format

```json
{
  "status": "healthy",
  "checks": [
    {
      "name": "providers",
      "status": "healthy",
      "duration_ms": 1.2
    }
  ],
  "version": "0.6.3",
  "uptime_seconds": 3600.5
}
```

### Kubernetes Configuration

```yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 10

readinessProbe:
  httpGet:
    path: /health/ready
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 5
```

## SLIs/SLOs

### Service Level Indicators

| SLI | Metric | Measurement |
|-----|--------|-------------|
| Availability | Service up | `up{job="mcp-hangar"}` |
| Latency | Tool call duration | P95 < 3s |
| Error Rate | Failed invocations | Error rate < 1% |
| Batch Success | Batch completion | Success rate > 95% |

### Recommended SLOs

| SLI | Target | Window |
|-----|--------|--------|
| Availability | 99.9% | 30 days |
| Latency (P95) | < 3s | 5 minutes |
| Error Rate | < 1% | 5 minutes |
| Batch Success | > 95% | 5 minutes |

### PromQL Queries

```promql
# Availability (service up ratio over 30d)
avg_over_time(up{job="mcp-hangar"}[30d])

# Error budget remaining
1 - (
  sum(increase(mcp_hangar_tool_call_errors_total[30d]))
  / sum(increase(mcp_hangar_tool_calls_total[30d]))
) / 0.01

# P95 latency
histogram_quantile(0.95,
  sum(rate(mcp_hangar_tool_call_duration_seconds_bucket[5m])) by (le)
)

# Batch success rate
sum(rate(mcp_hangar_batch_calls_total{result="success"}[5m]))
/ sum(rate(mcp_hangar_batch_calls_total[5m]))
```

## Troubleshooting

### Metrics Not Visible

1. Verify endpoint:

   ```bash
   curl http://localhost:8000/metrics | head -20
   ```

2. Check Prometheus targets at http://localhost:9090/targets

3. Verify network connectivity (use `host.docker.internal` for Docker on Mac/Windows)

### Alerts Not Firing

1. Check alert rules loaded:

   ```bash
   curl http://localhost:9090/api/v1/rules | jq '.data.groups[].name'
   ```

2. Verify metrics exist for alert expressions

3. Check Alertmanager connectivity:

   ```bash
   curl http://localhost:9093/api/v1/status
   ```

### High Consecutive Failures

If `MCPHangarHighConsecutiveFailures` fires:

1. Check provider logs for errors
2. Verify provider command/configuration
3. Test provider manually:

   ```bash
   mcp-hangar provider start <provider-id>
   ```

### Provider Start Errors

Common patterns and fixes:

| Error | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError` | Missing dependency | `pip install <package>` |
| `FileNotFoundError` | Wrong path | Check command in config |
| `PermissionError` | Not executable | `chmod +x <script>` |
| Exit code 137 | OOM killed | Increase memory limits |

## Best Practices

### Metrics

1. **Monitor the right things** - Focus on user-facing SLIs
2. **Set appropriate retention** - 15 days for metrics, 7 days for traces
3. **Avoid high cardinality** - Don't use unbounded values as labels

### Alerting

1. **Create runbooks** - Document response procedures
2. **Start conservative** - Tune thresholds based on baseline
3. **Test regularly** - Verify notification channels work
4. **Use severity correctly** - Critical = page, Warning = ticket

### Dashboards

1. **Layer information** - Overview -> Details -> Debug
2. **Include time selectors** - Allow drilling into incidents
3. **Add annotations** - Mark deployments and incidents

### Production Readiness Checklist

- [ ] Prometheus scraping MCP Hangar metrics
- [ ] Grafana dashboards imported and working
- [ ] Alertmanager configured with notification routes
- [ ] Critical alerts tested (e.g., stop service, verify page)
- [ ] Runbooks created for each alert
- [ ] Log aggregation configured (ELK, Loki, etc.)
- [ ] Tracing enabled and traces visible in Jaeger/Langfuse
