# Configuration Reference

All MCP Hangar behavior is controlled through a YAML configuration file and environment variables. The config file defaults to `config.yaml` in the working directory, overridden by the `MCP_CONFIG` environment variable. Environment variables take precedence over YAML settings where both exist.

## `MCP servers`

MCP Server definitions. Each key is a unique MCP server ID.

```yaml
mcp_servers:
  math:
    mode: subprocess
    command: [python, -m, math_server]
    idle_ttl_s: 300
    health_check_interval_s: 60

  my-api:
    mode: remote
    endpoint: https://api.example.com/mcp
    idle_ttl_s: 600

  my-container:
    mode: docker
    image: my-mcp:latest
    volumes:
      - ./data:/data:ro
    resources:
      memory: "512m"
      cpu: "1.0"
```

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `mode` | `str` | `"subprocess"` | subprocess, docker, remote | MCP Server mode. `container` and `podman` normalize to `docker`. |
| `command` | `list[str]` | -- | -- | Command for subprocess mode (required for subprocess) |
| `image` | `str` | -- | -- | Docker image for docker mode (required for docker) |
| `endpoint` | `str` | -- | -- | HTTP endpoint for remote mode (required for remote) |
| `env` | `dict[str, str]` | `{}` | -- | Environment variables passed to the MCP server process |
| `idle_ttl_s` | `int` | `300` | 1--86400 | Seconds of inactivity before the MCP server is auto-stopped |
| `health_check_interval_s` | `int` | `60` | 5--3600 | Interval between health checks in seconds |
| `max_consecutive_failures` | `int` | `3` | 1--100 | Consecutive health check failures before marking degraded |
| `volumes` | `list[str]` | `[]` | -- | Docker volume mounts (docker mode only) |
| `build` | `dict` | -- | -- | Docker build configuration (docker mode only) |
| `resources` | `dict` | `{memory: "512m", cpu: "1.0"}` | -- | Container resource limits (docker mode only) |
| `network` / `network_mode` | `str` | `"none"` | -- | Container network mode (docker mode only) |
| `read_only` | `bool` | `true` | -- | Read-only filesystem (docker mode only) |
| `user` | `str` | -- | -- | Container user. `"current"` maps to host `uid:gid` |
| `args` | `list[str]` | -- | -- | Container CMD override (docker mode only) |
| `description` | `str` | -- | -- | Human-readable MCP server description |
| `tools` | `list` or `dict` | -- | -- | Predefined tool schemas (list) or access policy (dict). See below. |
| `auth` | `dict` | -- | -- | HTTP auth configuration (remote mode only) |
| `tls` | `dict` | -- | -- | TLS configuration (remote mode only) |
| `http` | `dict` | -- | -- | HTTP transport configuration (remote mode only) |
| `max_concurrency` | `int` | -- | -- | Per-MCP server concurrency limit |

### `tools` dual format

The `tools` key accepts two formats depending on intent.

**List format** -- predefined tool schemas. The MCP server is not started to discover tools; schemas are served directly.

```yaml
mcp_servers:
  static-math:
    mode: subprocess
    command: [python, -m, math_server]
    tools:
      - name: add
        description: Add two numbers
        inputSchema:
          type: object
          properties:
            a: { type: number }
            b: { type: number }
```

**Dict format** -- tool access policy using fnmatch glob patterns.

```yaml
mcp_servers:
  restricted:
    mode: subprocess
    command: [python, -m, full_server]
    tools:
      allow_list:
        - "safe_*"
        - "read_*"
      deny_list:
        - "internal_*"
```

When `allow_list` is set, only matching tools are exposed. When only `deny_list` is set, all tools except matches are exposed.

## `execution`

System-wide concurrency limits.

```yaml
execution:
  max_concurrency: 50
  default_mcp_server_concurrency: 10
```

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `max_concurrency` | `int` | `50` | 0 = unlimited | System-wide maximum concurrent tool invocations |
| `default_mcp_server_concurrency` | `int` | `10` | -- | Default per-MCP server concurrency limit |

## `discovery`

Auto-discovery of MCP servers from external sources.

```yaml
discovery:
  enabled: true
  refresh_interval_s: 60
  auto_register: false
  sources:
    - type: docker
      mode: additive
    - type: filesystem
      mode: additive
      path: /etc/mcp/mcp_servers
      watch: true
```

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `enabled` | `bool` | -- | -- | Enable or disable discovery |
| `refresh_interval_s` | `int` | -- | -- | Interval between discovery scans in seconds |
| `auto_register` | `bool` | -- | -- | Automatically register discovered MCP servers |
| `sources` | `list[dict]` | `[]` | -- | Discovery source configurations (see below) |
| `security` | `dict` | -- | -- | Security constraints for discovery |
| `lifecycle` | `dict` | -- | -- | Lifecycle management for discovered MCP servers |

### `sources[]` entry

| Key | Type | Description |
|-----|------|-------------|
| `type` | `str` | Source type: `kubernetes`, `docker`, `filesystem`, `entrypoint` |
| `mode` | `str` | `additive` (only adds) or `authoritative` (adds and removes) |
| `path` / `pattern` | `str` | File path or glob pattern (filesystem source) |
| `watch` | `bool` | Enable file watching (filesystem source) |
| `namespaces` | `list[str]` | Kubernetes namespaces to scan |
| `label_selector` | `str` | Kubernetes label selector |
| `in_cluster` | `bool` | Use in-cluster Kubernetes config |
| `group` | `str` | Target group for discovered MCP servers |

### `security` sub-section

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `allowed_namespaces` | `list[str]` | -- | Kubernetes namespace allowlist |
| `denied_namespaces` | `list[str]` | -- | Kubernetes namespace denylist |
| `require_health_check` | `bool` | -- | Require health check before registration |
| `require_mcp_schema` | `bool` | -- | Require valid MCP schema |
| `max_mcp_servers_per_source` | `int` | -- | Maximum MCP servers per source |
| `max_registration_rate` | `int` | -- | Registration rate limit |
| `health_check_timeout_s` | `float` | -- | Health check timeout in seconds |
| `quarantine_on_failure` | `bool` | -- | Quarantine MCP servers that fail health checks |

### `lifecycle` sub-section

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `default_ttl_s` | `int` | -- | Default TTL for discovered MCP servers |
| `check_interval_s` | `int` | -- | Lifecycle check interval in seconds |
| `drain_timeout_s` | `int` | -- | Drain timeout before removal |

## `retry`

Retry policy for failed operations.

```yaml
retry:
  default_policy:
    max_attempts: 3
    backoff: exponential
    initial_delay: 1.0
    max_delay: 30.0
    retry_on:
      - ConnectionError
      - TimeoutError
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `default_policy.max_attempts` | `int` | -- | Maximum retry attempts |
| `default_policy.backoff` | `str` | -- | Backoff strategy (e.g., `exponential`) |
| `default_policy.initial_delay` | `float` | -- | Initial delay in seconds |
| `default_policy.max_delay` | `float` | -- | Maximum delay in seconds |
| `default_policy.retry_on` | `list[str]` | -- | Exception types to retry on |

## `event_store`

Persist domain events for audit and replay.

```yaml
event_store:
  enabled: true
  driver: sqlite
  path: data/events.db
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | `bool` | -- | Enable event persistence |
| `driver` | `str` | -- | Storage driver: `sqlite` or `memory` |
| `path` | `str` | -- | SQLite database path (sqlite driver only) |

## `knowledge_base`

Knowledge base storage backend.

```yaml
knowledge_base:
  enabled: true
  dsn: sqlite:///data/kb.db
  pool_size: 5
  cache_ttl_s: 300
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | `bool` | -- | Enable knowledge base |
| `dsn` | `str` | -- | Database connection string |
| `pool_size` | `int` | -- | Connection pool size |
| `cache_ttl_s` | `int` | -- | Cache TTL in seconds |

## `logging`

Log output configuration.

```yaml
logging:
  level: INFO
  json_format: false
  file: /var/log/mcp-hangar.log
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `level` | `str` | `"INFO"` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `json_format` | `bool` | `false` | Enable structured JSON logging |
| `file` | `str` | -- | Log file path |

## `health`

Global health check settings.

```yaml
health:
  enabled: true
  interval_s: 30
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | `bool` | -- | Enable health checks |
| `interval_s` | `int` | -- | Global health check interval in seconds |

## `observability`

Tracing and LLM observability integrations.

### `tracing` sub-section

```yaml
observability:
  tracing:
    enabled: true
    otlp_endpoint: http://localhost:4317
    service_name: mcp-hangar
    console_export: false
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | `bool` | -- | Enable OpenTelemetry tracing |
| `otlp_endpoint` | `str` | `"http://localhost:4317"` | OTLP exporter endpoint |
| `service_name` | `str` | `"mcp-hangar"` | Service name for traces |
| `jaeger_host` | `str` | -- | Jaeger agent host |
| `jaeger_port` | `int` | `6831` | Jaeger agent port |
| `console_export` | `bool` | -- | Export traces to console (development) |

### `langfuse` sub-section

```yaml
observability:
  langfuse:
    enabled: true
    public_key: pk-lf-...
    secret_key: ${LANGFUSE_SECRET_KEY}
    host: https://cloud.langfuse.com
    sample_rate: 1.0
    scrub_inputs: false
    scrub_outputs: false
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | `bool` | `false` | Enable Langfuse LLM observability |
| `public_key` | `str` | -- | Langfuse public API key |
| `secret_key` | `str` | -- | Langfuse secret key. Supports env var interpolation: `${LANGFUSE_SECRET_KEY}` |
| `host` | `str` | `"https://cloud.langfuse.com"` | Langfuse API host |
| `sample_rate` | `float` | `1.0` | Trace sampling rate (0.0--1.0) |
| `scrub_inputs` | `bool` | `false` | Redact sensitive data from tool inputs |
| `scrub_outputs` | `bool` | `false` | Redact sensitive data from tool outputs |

## `auth`

Authentication and authorization.

```yaml
auth:
  enabled: true
  allow_anonymous: false
  api_key:
    enabled: true
    header_name: X-API-Key
  oidc:
    enabled: false
    issuer: https://auth.example.com
    audience: mcp-hangar
  rate_limit:
    rps: 10
    burst: 20
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | `bool` | -- | Enable authentication |
| `allow_anonymous` | `bool` | -- | Allow unauthenticated requests |
| `api_key.enabled` | `bool` | -- | Enable API key authentication |
| `api_key.header_name` | `str` | -- | HTTP header name for API key |
| `oidc.enabled` | `bool` | -- | Enable OpenID Connect authentication |
| `oidc.issuer` | `str` | -- | OIDC issuer URL |
| `oidc.audience` | `str` | -- | Expected token audience |
| `oidc.subject_claim` | `str` | -- | JWT subject claim field |
| `oidc.groups_claim` | `str` | -- | JWT groups claim field |
| `oidc.email_claim` | `str` | -- | JWT email claim field |
| `oidc.tenant_claim` | `str` | -- | JWT tenant claim field |
| `opa.enabled` | `bool` | -- | Enable Open Policy Agent authorization |
| `opa.url` | `str` | -- | OPA server URL |
| `opa.policy_path` | `str` | -- | OPA policy path |
| `opa.timeout` | `float` | -- | OPA request timeout in seconds |
| `storage` | `dict` | -- | Auth storage configuration (driver, path, host, etc.) |
| `rate_limit` | `dict` | -- | Auth-specific rate limiting |
| `role_assignments` | `list[dict]` | -- | Role assignment rules |

## `config_reload`

Hot-reload configuration. See the [Hot-Reload Reference](hot-reload.md) for full details.

```yaml
config_reload:
  enabled: true
  use_watchdog: true
  interval_s: 5
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | `bool` | -- | Enable automatic config file watching |
| `use_watchdog` | `bool` | -- | Use watchdog library for file system events |
| `interval_s` | `int` | -- | Polling interval in seconds (fallback when watchdog unavailable) |

## `batch`

Limits for batch tool invocations via `hangar_call`.

```yaml
batch:
  max_calls: 100
  max_concurrency: 50
  default_timeout: 60
  max_timeout: 300
  max_response_size_bytes: 10485760      # 10 MB
  max_total_response_size_bytes: 52428800 # 50 MB
```

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `max_calls` | `int` | `100` | -- | Maximum calls per batch |
| `max_concurrency` | `int` | `50` | -- | Maximum parallel workers per batch |
| `default_timeout` | `float` | `60` | -- | Default batch timeout in seconds |
| `max_timeout` | `float` | `300` | -- | Maximum allowed batch timeout |
| `max_response_size_bytes` | `int` | `10485760` (10 MB) | -- | Maximum single response size before truncation |
| `max_total_response_size_bytes` | `int` | `52428800` (50 MB) | -- | Maximum total batch response size |

## `groups`

MCP Server groups are configured inside the `MCP servers` section with `mode: group`. A group load-balances requests across multiple member MCP servers.

```yaml
mcp_servers:
  llm-group:
    mode: group
    strategy: round_robin
    min_healthy: 1
    auto_start: true
    description: LLM mcp_server pool
    health:
      unhealthy_threshold: 2
      healthy_threshold: 1
    circuit_breaker:
      failure_threshold: 10
      reset_timeout_s: 60.0
    tools:
      allow_list: ["generate_*"]
    members:
      - id: llm-1
        mode: subprocess
        command: [python, -m, llm_server]
        weight: 70
        priority: 1
      - id: llm-2
        mode: subprocess
        command: [python, -m, llm_server]
        weight: 30
        priority: 2
```

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `mode` | `str` | -- | `"group"` | Must be `"group"` |
| `strategy` | `str` | `"round_robin"` | round_robin, weighted_round_robin, least_connections, random, priority | Load balancing strategy |
| `min_healthy` | `int` | `1` | >= 1 | Minimum healthy members for group HEALTHY state |
| `auto_start` | `bool` | `true` | -- | Auto-start members when the group is created |
| `description` | `str` | -- | -- | Group description |
| `health.unhealthy_threshold` | `int` | `2` | >= 1 | Consecutive failures before removing member from rotation |
| `health.healthy_threshold` | `int` | `1` | >= 1 | Consecutive successes before re-adding member to rotation |
| `circuit_breaker.failure_threshold` | `int` | `10` | >= 1 | Total group failures before the circuit opens |
| `circuit_breaker.reset_timeout_s` | `float` | `60.0` | >= 1.0 | Seconds before the circuit auto-resets |
| `tools` | `dict` | -- | -- | Group-level tool access policy (`allow_list`, `deny_list`) |
| `members` | `list[dict]` | `[]` | -- | Member MCP server configurations |

### Member configuration

Each member entry supports all standard MCP server keys (`mode`, `command`, `image`, `endpoint`, `env`, etc.) plus:

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `id` | `str` | -- | -- | Unique member ID (required) |
| `weight` | `int` | -- | 1--100 | Weight for weighted_round_robin and random strategies |
| `priority` | `int` | -- | 1--100 | Priority for priority strategy (lower number = higher priority) |

## Environment Variables

Environment variables override corresponding YAML settings. Variables follow the `MCP_` prefix convention. Third-party integrations (OpenTelemetry, Langfuse, Jaeger) use their standard prefixes.

### Server / CLI

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_CONFIG` | `"config.yaml"` | Path to YAML configuration file |
| `MCP_MODE` | `"stdio"` | Server mode: `stdio` or `http` |
| `MCP_HTTP_HOST` | `"0.0.0.0"` | HTTP bind host |
| `MCP_HTTP_PORT` | `8000` | HTTP bind port |
| `MCP_LOG_LEVEL` | `"INFO"` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `MCP_JSON_LOGS` | `"false"` | Enable structured JSON logging |

### Security / Runtime

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_RATE_LIMIT_RPS` | `"10"` | Rate limit: requests per second |
| `MCP_RATE_LIMIT_BURST` | `"20"` | Rate limit: burst size |
| `MCP_ALLOW_ABSOLUTE_PATHS` | `"false"` | Allow absolute paths in input validation |

### Persistence

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_PERSISTENCE_ENABLED` | `"false"` | Enable state persistence |
| `MCP_DATABASE_PATH` | `"data/mcp_hangar.db"` | SQLite database file path |
| `MCP_DATABASE_WAL` | `"true"` | Enable WAL mode for SQLite |
| `MCP_AUTO_RECOVER` | `"true"` | Auto-recover persisted state on startup |

### Observability / Tracing

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_TRACING_ENABLED` | `"true"` | Enable OpenTelemetry tracing |
| `MCP_TRACING_CONSOLE` | from config | Enable console trace export |
| `MCP_ENVIRONMENT` | `"development"` | Deployment environment label |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `"http://localhost:4317"` | OTLP exporter endpoint |
| `OTEL_SERVICE_NAME` | `"mcp-hangar"` | OpenTelemetry service name |
| `JAEGER_HOST` | -- | Jaeger agent host |
| `JAEGER_PORT` | `6831` | Jaeger agent port |

### Langfuse

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_LANGFUSE_ENABLED` | `"false"` | Enable Langfuse LLM observability |
| `LANGFUSE_PUBLIC_KEY` | -- | Langfuse public API key |
| `LANGFUSE_SECRET_KEY` | -- | Langfuse secret key (sensitive) |
| `LANGFUSE_HOST` | `"https://cloud.langfuse.com"` | Langfuse API host |
| `MCP_LANGFUSE_SAMPLE_RATE` | `"1.0"` | Trace sampling rate (0.0--1.0) |
| `MCP_LANGFUSE_SCRUB_INPUTS` | `"false"` | Redact sensitive tool inputs |
| `MCP_LANGFUSE_SCRUB_OUTPUTS` | `"false"` | Redact sensitive tool outputs |

!!! note "Legacy `HANGAR_*` prefix"
    The following legacy variables are supported for backward compatibility but `MCP_*` is the canonical prefix:
    `HANGAR_LANGFUSE_ENABLED` maps to `MCP_LANGFUSE_ENABLED`,
    `HANGAR_LANGFUSE_SAMPLE_RATE` maps to `MCP_LANGFUSE_SAMPLE_RATE`,
    `HANGAR_LANGFUSE_SCRUB_INPUTS` maps to `MCP_LANGFUSE_SCRUB_INPUTS`,
    `HANGAR_LANGFUSE_SCRUB_OUTPUTS` maps to `MCP_LANGFUSE_SCRUB_OUTPUTS`.

### Container Runtime

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_CONTAINER_RUNTIME` | -- | Force container runtime (`docker` or `podman`) |
| `MCP_CI_RELAX_VOLUME_PERMS` | -- | Relax volume permission checks in CI environments |
| `MCP_CONTAINER_INHERIT_STDERR` | -- | Inherit stderr from container processes |

### Auth

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_JWT_MAX_TOKEN_LIFETIME` | -- | Maximum JWT token lifetime |
