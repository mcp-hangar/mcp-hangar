# Observability Guide

This guide covers MCP Hangar's observability features: metrics, tracing, logging, and health checks.

## Quick Start

### Enable Full Observability Stack

```bash
# Start monitoring stack (Prometheus, Grafana, Jaeger)
docker compose -f docker-compose.monitoring.yml --profile tracing up -d

# Access dashboards
# Grafana: http://localhost:3000 (admin/admin)
# Prometheus: http://localhost:9090
# Jaeger: http://localhost:16686
```

### Configure MCP Hangar

```yaml
# config.yaml
logging:
  level: INFO
  json_format: true  # Enable for log aggregation

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
| Metric | Type | Description |
|--------|------|-------------|
| `mcp_registry_tool_calls_total` | Counter | Total tool invocations |
| `mcp_registry_tool_call_duration_seconds` | Histogram | Invocation latency |
| `mcp_registry_tool_call_errors_total` | Counter | Failed invocations |

#### Provider State
| Metric | Type | Description |
|--------|------|-------------|
| `mcp_registry_provider_state` | Gauge | Current provider state |
| `mcp_registry_cold_starts_total` | Counter | Cold start count |
| `mcp_registry_cold_start_duration_seconds` | Histogram | Cold start latency |

#### Health Checks
| Metric | Type | Description |
|--------|------|-------------|
| `mcp_registry_health_checks` | Counter | Health check executions |
| `mcp_registry_health_check_consecutive_failures` | Gauge | Consecutive failures |

#### Circuit Breaker
| Metric | Type | Description |
|--------|------|-------------|
| `mcp_registry_circuit_breaker_state` | Gauge | 0=closed, 1=open, 2=half_open |
| `mcp_registry_circuit_breaker_failures_total` | Counter | Circuit breaker trips |

### Prometheus Configuration

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'mcp-hangar'
    static_configs:
      - targets: ['mcp-hangar:8000']
    metrics_path: /metrics
    scrape_interval: 15s
```

### Grafana Dashboards

Pre-built dashboards are in `monitoring/grafana/dashboards/`:

- **Overview** - High-level health, latency, error rate
- **Providers** - Per-provider details
- **Discovery** - Auto-discovery metrics

Import via Grafana UI or use provisioning.

## Tracing

### OpenTelemetry Integration

MCP Hangar supports OpenTelemetry for distributed tracing:

```python
from mcp_hangar.observability import init_tracing, get_tracer

# Initialize at startup
init_tracing(
    service_name="mcp-hangar",
    otlp_endpoint="http://jaeger:4317",
)

# Create spans in your code
tracer = get_tracer(__name__)
with tracer.start_as_current_span("my_operation") as span:
    span.set_attribute("provider.id", provider_id)
    result = do_work()
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_TRACING_ENABLED` | `true` | Enable/disable tracing |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP collector endpoint |
| `OTEL_SERVICE_NAME` | `mcp-hangar` | Service name in traces |

### Trace Context Propagation

Trace context is automatically propagated through tool invocations:

```python
from mcp_hangar.observability import inject_trace_context, extract_trace_context

# Inject into outgoing request
headers = {}
inject_trace_context(headers)

# Extract from incoming request
context = extract_trace_context(request.headers)
```

### Viewing Traces

1. Open Jaeger UI: http://localhost:16686
2. Select service: `mcp-hangar`
3. Click "Find Traces"

## Logging

### Structured Logging

MCP Hangar uses structlog for structured JSON logging:

```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "level": "info",
  "event": "tool_invoked",
  "provider": "sqlite",
  "tool": "query",
  "duration_ms": 150,
  "trace_id": "abc123...",
  "service": "mcp-hangar"
}
```

### Log Correlation

Logs include trace IDs for correlation:

```python
from mcp_hangar.observability import get_current_trace_id

logger.info("operation_complete", trace_id=get_current_trace_id())
```

### Configuration

```yaml
# config.yaml
logging:
  level: INFO  # DEBUG, INFO, WARNING, ERROR
  json_format: true
  file: /var/log/mcp-hangar.log
```

## Health Checks

### Kubernetes Probes

MCP Hangar provides standard health endpoints:

| Endpoint | Purpose | Kubernetes Probe |
|----------|---------|------------------|
| `/health/live` | Is process alive? | livenessProbe |
| `/health/ready` | Can serve traffic? | readinessProbe |
| `/health/startup` | Finished initializing? | startupProbe |

### Kubernetes Configuration

```yaml
# deployment.yaml
containers:
  - name: mcp-hangar
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

    startupProbe:
      httpGet:
        path: /health/startup
        port: 8000
      failureThreshold: 30
      periodSeconds: 2
```

### Custom Health Checks

Register custom health checks:

```python
from mcp_hangar.observability import HealthCheck, get_health_endpoint

def check_database():
    # Return True if healthy
    return db.is_connected()

endpoint = get_health_endpoint()
endpoint.register_check(HealthCheck(
    name="database",
    check_fn=check_database,
    timeout_seconds=5.0,
    critical=True,  # If False, failure = degraded not unhealthy
))
```

## Alerting

### Alert Rules

Pre-configured alert rules in `monitoring/prometheus/alerts/`:

#### Critical Alerts (Page immediately)
- `MCPHangarAllProvidersDown` - No providers available
- `MCPHangarHighErrorRate` - Error rate > 10%
- `MCPHangarCircuitBreakerOpen` - Circuit breaker tripped
- `MCPHangarNotResponding` - Service unreachable

#### Warning Alerts (Notify team)
- `MCPHangarProviderDegraded` - Provider unhealthy
- `MCPHangarHighLatencyP95` - P95 latency > 5s
- `MCPHangarFrequentColdStarts` - Too many cold starts
- `MCPHangarLowAvailability` - Availability < 80%

### Alertmanager Configuration

Configure notification channels in `monitoring/alertmanager/alertmanager.yaml`:

```yaml
receivers:
  - name: 'critical-receiver'
    pagerduty_configs:
      - service_key: 'your-key'
    slack_configs:
      - api_url: 'https://hooks.slack.com/...'
        channel: '#alerts-critical'
```

## SLIs/SLOs

### Recommended SLOs

| SLI | Target | Window |
|-----|--------|--------|
| Availability | 99.9% | 30 days |
| Latency P95 | < 2s | 5 minutes |
| Error Rate | < 1% | 5 minutes |

### Error Budget

```promql
# Error budget remaining
1 - (
  sum(rate(mcp_registry_errors_total[30d])) /
  sum(rate(mcp_registry_tool_calls_total[30d]))
) / 0.001  # 99.9% SLO = 0.1% error budget
```

## Troubleshooting

### Common Issues

#### No metrics visible
1. Check `/metrics` endpoint is accessible
2. Verify Prometheus can reach MCP Hangar
3. Check firewall/network policies

#### Traces not appearing
1. Verify `MCP_TRACING_ENABLED=true`
2. Check OTLP endpoint is reachable
3. Look for errors in logs

#### High cardinality warnings
1. Review label values
2. Avoid user-provided values in labels
3. Use aggregation in queries

### Debug Mode

Enable verbose logging:

```bash
MCP_LOG_LEVEL=DEBUG python -m mcp_hangar.server
```

## Best Practices

1. **Use structured logging** - JSON format for aggregation
2. **Set appropriate retention** - 15 days for metrics, 7 days for traces
3. **Create runbooks** - Document response procedures for each alert
4. **Test alerts** - Regularly verify alerting works
5. **Monitor the monitors** - Alert on Prometheus/Grafana health
