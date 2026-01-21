# HTTP Transport for Remote Providers

MCP Hangar supports connecting to remote MCP providers exposed via HTTP/HTTPS endpoints. This enables integration with MCP servers deployed as standalone HTTP services in enterprise environments.

## Overview

HTTP transport allows MCP Hangar to act as a gateway to remote MCP providers, providing:

- **Unified interface**: Same API for local and remote providers
- **Authentication**: Support for API keys, Bearer tokens, and Basic auth
- **TLS/HTTPS**: Full support for custom CA certificates
- **Connection management**: Automatic retries, timeouts, and connection pooling
- **Observability**: HTTP-specific metrics integrated with existing pipeline

## Configuration

### Basic Remote Provider

```yaml
providers:
  remote-math:
    mode: remote
    endpoint: https://mcp-server.example.com/mcp
    description: "Remote math provider"
```

### Authentication Options

#### No Authentication

```yaml
providers:
  public-provider:
    mode: remote
    endpoint: http://localhost:8080/mcp
```

#### API Key Authentication

```yaml
providers:
  api-key-provider:
    mode: remote
    endpoint: https://api.example.com/mcp
    auth:
      type: api_key
      api_key: ${MCP_API_KEY}  # Environment variable
      api_key_header: X-API-Key  # Default header name
```

#### Bearer Token Authentication

```yaml
providers:
  bearer-provider:
    mode: remote
    endpoint: https://secure.example.com/mcp
    auth:
      type: bearer
      bearer_token: ${MCP_BEARER_TOKEN}
```

#### Basic Authentication

```yaml
providers:
  basic-auth-provider:
    mode: remote
    endpoint: https://internal.example.com/mcp
    auth:
      type: basic
      username: ${MCP_USERNAME}
      password: ${MCP_PASSWORD}
```

### TLS Configuration

#### Custom CA Certificate

```yaml
providers:
  private-provider:
    mode: remote
    endpoint: https://private.example.com:8443/mcp
    tls:
      verify_ssl: true
      ca_cert_path: /etc/ssl/certs/internal-ca.pem
```

#### Disable SSL Verification (Development Only!)

```yaml
providers:
  dev-provider:
    mode: remote
    endpoint: https://dev.example.com/mcp
    tls:
      verify_ssl: false  # WARNING: Only for development!
```

### HTTP Transport Options

```yaml
providers:
  tuned-provider:
    mode: remote
    endpoint: https://api.example.com/mcp
    http:
      connect_timeout: 10.0  # Connection timeout in seconds
      read_timeout: 60.0     # Read timeout in seconds
      max_retries: 5         # Maximum retry attempts
      retry_backoff_factor: 0.5  # Exponential backoff factor
      headers:               # Additional headers
        X-Request-Source: mcp-hangar
        X-Correlation-Id: ${REQUEST_ID:-default}
```

## Environment Variable Interpolation

Configuration values support environment variable interpolation using the `${VAR_NAME}` syntax:

- `${VAR_NAME}` - Replace with environment variable value
- `${VAR_NAME:-default}` - Use default value if not set

Example:

```yaml
providers:
  secure-provider:
    mode: remote
    endpoint: ${MCP_ENDPOINT:-https://localhost:8080/mcp}
    auth:
      type: bearer
      bearer_token: ${MCP_TOKEN}
```

## SSE Streaming Support

HTTP transport supports Server-Sent Events (SSE) for streaming responses from MCP providers. This is automatically detected based on the `Content-Type` header.

When a provider responds with `Content-Type: text/event-stream`, the client:

1. Opens an SSE connection
2. Reads events until the response for the request ID is received
3. Handles timeouts gracefully

## Health Checks

Remote providers support the same health check mechanism as local providers:

```yaml
providers:
  remote-with-health:
    mode: remote
    endpoint: https://api.example.com/mcp
    health_check_interval_s: 30
    max_consecutive_failures: 3
```

Health checks use the MCP `initialize` or `tools/list` methods to verify connectivity.

## Metrics

HTTP transport exposes the following metrics:

| Metric | Type | Description |
|--------|------|-------------|
| `mcp_registry_http_requests_total` | Counter | Total HTTP requests by provider, method, status |
| `mcp_registry_http_request_duration_seconds` | Histogram | Request latency |
| `mcp_registry_http_errors_total` | Counter | HTTP errors by type |
| `mcp_registry_http_retries_total` | Counter | Retry attempts |
| `mcp_registry_http_connection_pool_size` | Gauge | Connection pool size |
| `mcp_registry_http_sse_streams_active` | Gauge | Active SSE streams |
| `mcp_registry_http_sse_events_total` | Counter | SSE events received |

## Error Handling

### Connection Errors

When a remote provider is unavailable:

1. The provider transitions to `DEAD` or `DEGRADED` state
2. Backoff with exponential retry is applied
3. Health checks continue to monitor recovery

### Authentication Failures

HTTP 401/403 responses are logged and cause provider degradation. Check:

1. Credentials in environment variables
2. Token expiration
3. API key validity

### Timeout Handling

Timeouts are configurable per-provider:

- `connect_timeout`: Time to establish connection
- `read_timeout`: Time to receive response

On timeout, the request fails and the provider health is affected.

## Security Considerations

1. **Never store secrets in config files** - Use environment variables
2. **Use HTTPS in production** - HTTP is only for local development
3. **Enable SSL verification** - Disable only for development with self-signed certificates
4. **Rotate credentials regularly** - Especially for production environments

## Example: Complete Configuration

```yaml
providers:
  production-math:
    mode: remote
    endpoint: https://mcp-math.production.example.com/mcp
    description: "Production math service with bearer auth"
    auth:
      type: bearer
      bearer_token: ${MATH_SERVICE_TOKEN}
    tls:
      verify_ssl: true
      ca_cert_path: /etc/ssl/certs/company-ca.pem
    http:
      connect_timeout: 5.0
      read_timeout: 30.0
      max_retries: 3
      headers:
        X-Service-Name: mcp-hangar
        X-Environment: production
    idle_ttl_s: 600
    health_check_interval_s: 30
    max_consecutive_failures: 3
    tools:
      - name: add
        description: Add two numbers
        inputSchema:
          type: object
          properties:
            a: { type: number }
            b: { type: number }
          required: [a, b]
      - name: multiply
        description: Multiply two numbers
        inputSchema:
          type: object
          properties:
            a: { type: number }
            b: { type: number }
          required: [a, b]

logging:
  level: INFO
  json_format: true
```
