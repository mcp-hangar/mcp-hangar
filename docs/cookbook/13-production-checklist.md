# 13 -- Production Checklist

> Before you go live, walk through this list.

## Security

- [ ] TLS termination configured (reverse proxy or load balancer)
- [ ] `auth.enabled: true` and `auth.allow_anonymous: false`
- [ ] API keys created for each service principal
- [ ] RBAC roles assigned with least-privilege
- [ ] Tool access policies set for sensitive tools
- [ ] `MCP_AUTH_SECRET` set to a stable, random value (not auto-generated)
- [ ] Secrets use environment variable interpolation (`${VAR}`), not plain text in config
- [ ] Docker providers use `read_only: true` and `network: none` where possible

## Reliability

- [ ] Health checks enabled on all providers (`health_check_interval_s`)
- [ ] Circuit breaker thresholds tuned (`max_consecutive_failures`)
- [ ] Provider groups configured for critical providers (at least 2 members)
- [ ] `min_healthy` set to match your SLA requirements
- [ ] Idle TTL set appropriately (300s for subprocess, 600s for containers)
- [ ] Rate limiting enabled to prevent overload
- [ ] Event store configured (`event_store.driver: sqlite`)

## Observability

- [ ] Prometheus scraping `/metrics` endpoint
- [ ] Grafana dashboards imported from `monitoring/grafana/`
- [ ] Alertmanager rules configured for:
  - Provider state transitions to DEAD
  - Circuit breaker OPEN events
  - Health check failure rate above threshold
  - Tool call error rate above threshold
- [ ] Structured JSON logging enabled (`MCP_JSON_LOGS=true`)
- [ ] Log level set to `INFO` for production (`MCP_LOG_LEVEL=INFO`)

## Configuration

- [ ] Config file validated (`mcp-hangar validate config.yaml`)
- [ ] Hot-reload tested (`kill -HUP` or file edit)
- [ ] Config backup strategy in place (use `/api/config/backup`)
- [ ] Environment-specific configs separated (dev/staging/prod)

## Deployment

- [ ] Running behind a reverse proxy (nginx, Caddy, Envoy)
- [ ] Health check endpoint exposed for orchestrator (`/api/system`)
- [ ] Graceful shutdown configured (SIGTERM handling)
- [ ] Resource limits set (memory, CPU) for container deployments
- [ ] Persistent volume for event store SQLite database
- [ ] Docker image pinned to specific version tag, not `latest`

## Kubernetes (if applicable)

- [ ] MCP-Hangar Operator installed
- [ ] CRDs applied (`MCPProvider`, `MCPProviderGroup`, `MCPDiscoverySource`)
- [ ] RBAC (Kubernetes) configured for operator service account
- [ ] Network policies restricting provider-to-provider communication
- [ ] Resource requests and limits in Helm values
- [ ] PodDisruptionBudget for Hangar deployment

## Testing

- [ ] Failover tested: kill a primary provider, verify backup takes over
- [ ] Cold start tested: invoke a tool on a cold provider, verify latency
- [ ] Rate limit tested: flood API, verify 429 responses
- [ ] Auth tested: invalid key returns 401, insufficient role returns 403
- [ ] Config reload tested: edit config.yaml, verify changes apply
- [ ] Recovery tested: kill all providers, verify they reinitialize

## Runbook

- [ ] Incident response documented
- [ ] Provider restart procedure documented
- [ ] Config rollback procedure documented
- [ ] Contact list for provider owners maintained
