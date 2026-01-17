# Observability Guide

This guide covers MCP Hangar's observability features: metrics, tracing, logging, and health checks.

## Table of Contents

- [Quick Start](#quick-start)
- [Metrics](#metrics)
- [Tracing](#tracing)
- [Langfuse Integration](#langfuse-integration)
- [Logging](#logging)
- [Health Checks](#health-checks)
- [Alerting](#alerting)
- [SLIs/SLOs](#slisslos)
- [Troubleshooting](#troubleshooting)
- [Best Practices](#best-practices)

## Quick Start

### Prerequisites

For full tracing support, install OpenTelemetry dependencies:

```bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp
```

### Enable Full Observability Stack

```bash
# Start monitoring stack (Prometheus, Grafana, Jaeger)
docker compose -f docker-compose.monitoring.yml --profile tracing up -d
```

Access dashboards:
- **Grafana**: http://localhost:3000 (admin/admin)
- **Prometheus**: http://localhost:9090
- **Jaeger**: http://localhost:16686

### Configure MCP Hangar

```yaml
# config.yaml
logging:
  level: INFO
  json_format: true

observability:
  tracing:
    enabled: true
    otlp_endpoint: http://localhost:4317
  metrics:
    enabled: true
    endpoint: /metrics
```

## Metrics

### Available Metrics

MCP Hangar exports Prometheus metrics at `/metrics`:

#### Tool Invocations

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mcp_registry_tool_calls_total` | Counter | provider, tool | Total tool invocations |
| `mcp_registry_tool_call_duration_seconds` | Histogram | provider, tool | Invocation latency |
| `mcp_registry_tool_call_errors_total` | Counter | provider, tool, error_type | Failed invocations |

#### Provider State

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mcp_registry_provider_state` | Gauge | provider, state | Current provider state |
| `mcp_registry_cold_starts_total` | Counter | provider, mode | Cold start count |
| `mcp_registry_cold_start_duration_seconds` | Histogram | provider, mode | Cold start latency |

#### Health Checks

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mcp_registry_health_checks` | Counter | provider, result | Health check executions |
| `mcp_registry_health_check_consecutive_failures` | Gauge | provider | Consecutive failures |

#### Circuit Breaker

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mcp_registry_circuit_breaker_state` | Gauge | provider | State: 0=closed, 1=open, 2=half_open |
| `mcp_registry_circuit_breaker_failures_total` | Counter | provider | Circuit breaker trip count |

#### Retry Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mcp_registry_retry_attempts_total` | Counter | provider, tool, attempt_number | Retry attempts |
| `mcp_registry_retry_exhausted_total` | Counter | provider, tool | Retries exhausted |
| `mcp_registry_retry_succeeded_total` | Counter | provider, tool, attempt_number | Successful retries |

### Prometheus Configuration

```yaml
# prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'mcp-hangar'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: /metrics
    scrape_interval: 10s
```

### Grafana Dashboards

Pre-built dashboards are in `monitoring/grafana/dashboards/`:

| Dashboard | Description |
|-----------|-------------|
| **overview.json** | High-level health, latency percentiles, error rates |
| **providers.json** | Per-provider details and state transitions |
| **discovery.json** | Auto-discovery metrics and source health |

Import via Grafana UI or use the provisioning configuration in `monitoring/grafana/provisioning/`.

## Tracing

### OpenTelemetry Integration

MCP Hangar supports distributed tracing via OpenTelemetry:

```python
from mcp_hangar.observability import init_tracing, get_tracer

# Initialize once at application startup
init_tracing(
    service_name="mcp-hangar",
    otlp_endpoint="http://localhost:4317",
)

# Get a tracer for your module
tracer = get_tracer(__name__)

# Create spans for operations
with tracer.start_as_current_span("process_request") as span:
    span.set_attribute("request.id", request_id)
    span.set_attribute("provider.id", provider_id)
    result = process_request()
```

### Using trace_span Context Manager

For simpler usage:

```python
from mcp_hangar.observability import trace_span

with trace_span("my_operation", {"key": "value"}) as span:
    span.add_event("checkpoint_reached")
    do_work()
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_TRACING_ENABLED` | `true` | Enable/disable tracing |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP collector endpoint (gRPC) |
| `OTEL_SERVICE_NAME` | `mcp-hangar` | Service name in traces |
| `MCP_ENVIRONMENT` | `development` | Deployment environment tag |

### Trace Context Propagation

Propagate trace context across service boundaries:

```python
from mcp_hangar.observability import inject_trace_context, extract_trace_context

# Inject context into outgoing request headers
headers = {}
inject_trace_context(headers)
# headers now contains 'traceparent' and 'tracestate'

# Extract context from incoming request
context = extract_trace_context(request_headers)
```

### Getting Current Trace Information

```python
from mcp_hangar.observability import get_current_trace_id, get_current_span_id

# Get current trace ID for logging correlation
trace_id = get_current_trace_id()  # Returns hex string or None
span_id = get_current_span_id()    # Returns hex string or None
```

### Viewing Traces

1. Open Jaeger UI at http://localhost:16686
2. Select service `mcp-hangar` from dropdown
3. Set time range and click **Find Traces**
4. Click on a trace to see the span tree

## Langfuse Integration

MCP Hangar integrates with [Langfuse](https://langfuse.com) for LLM-specific observability, providing end-to-end tracing of tool invocations from your LLM application through MCP Hangar to individual providers.

### Why Langfuse?

| Feature | Benefit |
|---------|---------|
| **End-to-end traces** | See the complete journey from LLM prompt → tool call → provider response |
| **Cost attribution** | Track costs per provider, tool, user, or session |
| **Latency analysis** | Identify slow providers and optimize performance |
| **Quality scoring** | Correlate provider health with LLM response quality |
| **Evals** | Run automated evaluations on tool outputs |

### Installation

```bash
pip install mcp-hangar[observability]
```

### Configuration

#### Via Environment Variables

```bash
export HANGAR_LANGFUSE_ENABLED=true
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
export LANGFUSE_HOST=https://cloud.langfuse.com  # or self-hosted URL
```

#### Via config.yaml

```yaml
observability:
  langfuse:
    enabled: true
    public_key: ${LANGFUSE_PUBLIC_KEY}
    secret_key: ${LANGFUSE_SECRET_KEY}
    host: https://cloud.langfuse.com
    sample_rate: 1.0          # 0.0-1.0, fraction of traces to sample
    scrub_inputs: false       # Redact sensitive input data
    scrub_outputs: false      # Redact sensitive output data
```

### Trace Propagation

To correlate traces from your LLM application with MCP Hangar, pass a `trace_id` when invoking tools:

```python
from mcp_hangar.application.services import TracedProviderService

# Invoke with trace context from your LLM application
result = traced_service.invoke_tool(
    provider_id="math",
    tool_name="add",
    arguments={"a": 1, "b": 2},
    trace_id="your-langfuse-trace-id",    # Correlates with LLM trace
    user_id="user-123",                    # For cost attribution
    session_id="session-456",              # For grouping related calls
)
```

### What Gets Traced

| Event | Recorded Data |
|-------|---------------|
| **Tool invocation** | Provider, tool, input params, output, latency, success/error |
| **Health check** | Provider, healthy status, latency |

### Recorded Scores

| Score Name | Description |
|------------|-------------|
| `tool_success` | 1.0 for success, 0.0 for error |
| `tool_latency_ms` | Invocation latency in milliseconds |
| `provider_healthy` | 1.0 if healthy, 0.0 if unhealthy |
| `health_check_latency_ms` | Health check latency |

### Using TracedProviderService

The `TracedProviderService` wraps `ProviderService` to automatically trace operations:

```python
from mcp_hangar.application.services import ProviderService, TracedProviderService
from mcp_hangar.infrastructure.observability import LangfuseObservabilityAdapter, LangfuseConfig

# Create the observability adapter
langfuse_config = LangfuseConfig(
    enabled=True,
    public_key="pk-lf-...",
    secret_key="sk-lf-...",
)
observability = LangfuseObservabilityAdapter(langfuse_config)

# Wrap your existing service
traced_service = TracedProviderService(
    provider_service=provider_service,
    observability=observability,
)

# All tool invocations are now traced
result = traced_service.invoke_tool("math", "add", {"a": 1, "b": 2})
```

### GDPR Compliance

Enable input/output scrubbing to avoid sending sensitive data to Langfuse:

```yaml
observability:
  langfuse:
    enabled: true
    scrub_inputs: true    # Only sends parameter keys, not values
    scrub_outputs: true   # Only sends output structure, not content
```

### Viewing Traces in Langfuse

1. Open Langfuse dashboard at https://cloud.langfuse.com
2. Navigate to **Traces**
3. Filter by:
   - `metadata.mcp_hangar = true` for MCP Hangar traces
   - `metadata.provider = math` for specific providers
4. Click on a trace to see:
   - Input parameters
   - Output results
   - Latency breakdown
   - Recorded scores

### Combining with OpenTelemetry

Langfuse and OpenTelemetry can run simultaneously. Langfuse focuses on LLM-specific observability while OpenTelemetry provides infrastructure-level tracing:

```yaml
observability:
  tracing:
    enabled: true
    otlp_endpoint: http://localhost:4317
  langfuse:
    enabled: true
    public_key: ${LANGFUSE_PUBLIC_KEY}
    secret_key: ${LANGFUSE_SECRET_KEY}
```

## Logging

### Structured Logging

MCP Hangar uses structlog for structured JSON logging:

```json
{
  "timestamp": "2026-01-09T10:30:00.123Z",
  "level": "info",
  "event": "tool_invoked",
  "provider": "sqlite",
  "tool": "query",
  "duration_ms": 150,
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "service": "mcp-hangar"
}
```

### Log Correlation with Traces

Include trace IDs in logs for correlation:

```python
from mcp_hangar.observability import get_current_trace_id
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

def handle_request():
    trace_id = get_current_trace_id()
    logger.info("processing_request", trace_id=trace_id, request_id=req_id)
```

### Configuration

```yaml
# config.yaml
logging:
  level: INFO          # DEBUG, INFO, WARNING, ERROR, CRITICAL
  json_format: true    # Enable JSON output for log aggregation
  file: logs/mcp-hangar.log  # Optional file output
```

Environment variable override:

```bash
MCP_LOG_LEVEL=DEBUG python -m mcp_hangar.server
```

## Health Checks

### HTTP Endpoints

MCP Hangar provides standard health endpoints compatible with Kubernetes and other orchestrators:

| Endpoint | HTTP Method | Purpose | Use Case |
|----------|-------------|---------|----------|
| `/health/live` | GET | Liveness check | Container restart decisions |
| `/health/ready` | GET | Readiness check | Traffic routing decisions |
| `/health/startup` | GET | Startup check | Startup completion gate |

### Response Format

```json
{
  "status": "healthy",
  "checks": [
    {
      "name": "providers",
      "status": "healthy",
      "duration_ms": 1.2,
      "message": "Check passed"
    }
  ],
  "version": "0.1.0",
  "uptime_seconds": 3600.5
}
```

### Container Orchestration Configuration

#### Kubernetes

```yaml
apiVersion: v1
kind: Pod
spec:
  containers:
    - name: mcp-hangar
      image: mcp-hangar:latest
      ports:
        - containerPort: 8000
      livenessProbe:
        httpGet:
          path: /health/live
          port: 8000
        initialDelaySeconds: 5
        periodSeconds: 10
        failureThreshold: 3
      readinessProbe:
        httpGet:
          path: /health/ready
          port: 8000
        initialDelaySeconds: 10
        periodSeconds: 5
        failureThreshold: 3
      startupProbe:
        httpGet:
          path: /health/startup
          port: 8000
        failureThreshold: 30
        periodSeconds: 2
```

#### Docker Compose

```yaml
services:
  mcp-hangar:
    image: mcp-hangar:latest
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health/ready"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

> **Note**: If curl is not available in your image, use Python:
> ```yaml
> healthcheck:
>   test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/ready', timeout=5)"]
> ```

### Custom Health Checks

Register application-specific health checks:

```python
from mcp_hangar.observability import HealthCheck, get_health_endpoint

def check_database_connection():
    """Return True if database is reachable."""
    try:
        db.execute("SELECT 1")
        return True
    except Exception:
        return False

# Register the check
endpoint = get_health_endpoint()
endpoint.register_check(HealthCheck(
    name="database",
    check_fn=check_database_connection,
    timeout_seconds=5.0,
    critical=True,  # False = degraded instead of unhealthy on failure
))

# Mark startup complete when ready
endpoint.mark_startup_complete()
```

### Async Health Checks

```python
async def check_external_service():
    """Async health check example."""
    async with aiohttp.ClientSession() as session:
        async with session.get("http://external-service/health") as resp:
            return resp.status == 200

endpoint.register_check(HealthCheck(
    name="external_service",
    check_fn=check_external_service,
    timeout_seconds=3.0,
    critical=False,  # Non-critical: failure results in degraded state
))
```

## Alerting

### Alert Rules

Pre-configured alert rules are in `monitoring/prometheus/alerts/`:

#### Critical Alerts (Immediate Response Required)

| Alert | Condition | Description |
|-------|-----------|-------------|
| `MCPHangarAllProvidersDown` | No ready providers for 1m | Complete service outage |
| `MCPHangarHighErrorRate` | Error rate > 10% for 2m | Significant failures |
| `MCPHangarCircuitBreakerOpen` | Any circuit breaker open | Provider isolation triggered |
| `MCPHangarNotResponding` | Scrape failures for 1m | Service unreachable |
| `MCPHangarStartupFailed` | Repeated startup failures | Provider cannot initialize |

#### Warning Alerts (Investigation Required)

| Alert | Condition | Description |
|-------|-----------|-------------|
| `MCPHangarProviderDegraded` | Provider degraded for 5m | Provider experiencing issues |
| `MCPHangarHighLatencyP95` | P95 > 5s for 5m | Performance degradation |
| `MCPHangarFrequentColdStarts` | Cold start rate > 0.1/s | Consider increasing idle TTL |
| `MCPHangarDiscoverySourceUnhealthy` | Source unhealthy for 5m | Discovery issues |
| `MCPHangarLowAvailability` | Availability < 80% for 5m | Multiple providers affected |
| `MCPHangarRetryExhaustion` | High retry exhaustion rate | Persistent failures |

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
        severity: critical

  - name: 'slack'
    slack_configs:
      - api_url: '<your-slack-webhook-url>'
        channel: '#alerts'
        title: '{{ .CommonAnnotations.summary }}'
        text: '{{ .CommonAnnotations.description }}'
```

## SLIs/SLOs

### Service Level Indicators

| SLI | Metric | Good Event |
|-----|--------|------------|
| Availability | Provider ready state | `mcp_registry_provider_state{state="ready"}` |
| Latency | Tool invocation duration | Request < 2s |
| Error Rate | Failed invocations | `mcp_registry_errors_total` |

### Recommended SLOs

| SLI | Target | Measurement Window |
|-----|--------|-------------------|
| Availability | 99.9% | 30 days rolling |
| Latency (P95) | < 2s | 5 minute window |
| Error Rate | < 1% | 5 minute window |

### Error Budget Calculation

```promql
# Error budget remaining (1.0 = full budget, 0.0 = exhausted)
1 - (
  sum(increase(mcp_registry_errors_total[30d])) /
  sum(increase(mcp_registry_tool_calls_total[30d]))
) / 0.001
```

### Availability Query

```promql
# Current availability ratio
sum(mcp_registry_provider_state{state="ready"}) /
sum(mcp_registry_provider_state)
```

## Troubleshooting

### Metrics Not Visible

1. **Verify endpoint accessibility**:
   ```bash
   curl http://localhost:8000/metrics
   ```

2. **Check Prometheus targets**:
   - Open http://localhost:9090/targets
   - Verify MCP Hangar target is `UP`

3. **Review Prometheus logs**:
   ```bash
   docker logs mcp-prometheus 2>&1 | grep -i error
   ```

### Traces Not Appearing

1. **Verify tracing is enabled**:
   ```bash
   echo $MCP_TRACING_ENABLED  # Should be 'true' or unset
   ```

2. **Check OTLP endpoint connectivity**:
   ```bash
   curl -v http://localhost:4317
   ```

3. **Look for initialization errors**:
   ```bash
   grep -i "tracing" logs/mcp-hangar.log
   ```

4. **Verify OpenTelemetry packages installed**:
   ```bash
   pip list | grep opentelemetry
   ```

### Health Check Failures

1. **Get detailed health status**:
   ```bash
   curl -s http://localhost:8000/health/ready | jq .
   ```

2. **Check individual check results**:
   ```python
   endpoint = get_health_endpoint()
   result = endpoint.get_last_result("providers")
   print(result.to_dict())
   ```

### High Cardinality Warnings

1. Review metric label values for unbounded sets
2. Avoid user-provided values in labels
3. Use label aggregation in queries:
   ```promql
   sum by (provider) (rate(mcp_registry_tool_calls_total[5m]))
   ```

## Best Practices

### Metrics

1. **Use meaningful labels** - Include provider, tool, and outcome
2. **Avoid high cardinality** - Don't use request IDs or timestamps as labels
3. **Set retention appropriately** - 15 days for metrics, 7 days for traces

### Tracing

1. **Initialize early** - Call `init_tracing()` at application startup
2. **Use semantic attributes** - Follow OpenTelemetry conventions
3. **Propagate context** - Inject/extract for cross-service traces

### Alerting

1. **Create runbooks** - Document response procedures for each alert
2. **Test alerts regularly** - Verify notification channels work
3. **Tune thresholds** - Adjust based on baseline behavior

### Health Checks

1. **Keep checks fast** - Use short timeouts (< 5s)
2. **Distinguish critical vs non-critical** - Use `critical=False` for degraded states
3. **Monitor the monitors** - Alert on Prometheus/Grafana health
