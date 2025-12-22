# Security

> **Note:** For reporting security vulnerabilities, see [SECURITY.md](../SECURITY.md) in the project root.

This document describes the security features and best practices for MCP Hangar.

## Overview

MCP Hangar implements defense-in-depth security:

1. **Input Validation** - All inputs validated at API boundaries
2. **Injection Prevention** - Commands and arguments sanitized
3. **Rate Limiting** - Token bucket algorithm prevents abuse
4. **Secrets Management** - Sensitive data masked in logs
5. **Audit Logging** - Security events logged for monitoring

## Input Validation

All inputs are validated using the `InputValidator` class:

```python
from mcp_hangar.domain.security.input_validator import (
    validate_provider_id,
    validate_tool_name,
    validate_arguments,
    validate_command,
)

result = validate_provider_id("my_provider")
if not result.valid:
    print(f"Validation failed: {result.errors}")
```

### Validation Rules

| Input Type | Rules |
|------------|-------|
| Provider ID | Alphanumeric, hyphens, underscores; 1-64 chars |
| Tool Name | Alphanumeric, underscores, dots, slashes; 1-128 chars |
| Arguments | Dict; max 1MB; max 10 nesting depth |
| Timeout | 0.1-3600 seconds |
| Command | Non-empty list; no shell metacharacters |
| Docker Image | Valid format; no injection patterns |

### Blocked Patterns

The validator blocks injection patterns:

- Command chaining: `;`, `&&`, `||`
- Pipes: `|`
- Command substitution: `` ` ``, `$()`
- Variable expansion: `${}`
- Redirects: `>`, `<`
- Control characters: `\n`, `\r`, `\0`

## Command Injection Prevention

### Subprocess Launcher

```python
from mcp_hangar.domain.services.provider_launcher import SubprocessLauncher

launcher = SubprocessLauncher(
    allowed_commands={"python", "python3", "node"},
    blocked_commands={"rm", "sudo", "bash", "sh"},
    allow_absolute_paths=False,
    filter_sensitive_env=True,
)
```

Security features:
- Command validation before execution
- Shell metacharacter sanitization
- Blocked command list
- Environment variable filtering
- Always uses `shell=False`

### Docker Launcher

```python
from mcp_hangar.domain.services.provider_launcher import DockerLauncher

launcher = DockerLauncher(
    allowed_registries={"ghcr.io", "docker.io"},
    enable_network=False,
    memory_limit="512m",
    read_only=True,
    drop_capabilities=True,
)
```

Security flags applied:
- `--network none`
- `--memory`
- `--cpus`
- `--read-only`
- `--cap-drop ALL`
- `--security-opt no-new-privileges`

## Rate Limiting

```python
from mcp_hangar.domain.security.rate_limiter import InMemoryRateLimiter, RateLimitConfig

config = RateLimitConfig(
    requests_per_second=10.0,
    burst_size=20,
)

limiter = InMemoryRateLimiter(config)
result = limiter.consume("client_key")
if not result.allowed:
    print(f"Rate limited. Retry after: {result.retry_after}s")
```

Configure via environment:

```bash
export MCP_RATE_LIMIT_RPS=10
export MCP_RATE_LIMIT_BURST=20
```

Rate limits are applied per-operation: `registry_invoke:{provider}`, etc.

## Secrets Management

```python
from mcp_hangar.domain.security.secrets import SecureEnvironment, mask_sensitive_value

mask_sensitive_value("secret123")  # "secr********"

env = SecureEnvironment({
    "PATH": "/usr/bin",
    "API_KEY": "secret-key-123",
})
env.get_masked("API_KEY")  # "secr********"
```

Sensitive key patterns (automatically masked):
- `password`, `passwd`, `secret`
- `api_key`, `apikey`, `auth_token`
- `access_token`, `bearer`, `credential`
- `private_key`, `*_token`, `*_key`, `*_secret`

## Security Audit Logging

```python
from mcp_hangar.application.event_handlers import SecurityEventHandler

handler = SecurityEventHandler(sink=sink)
handler.log_injection_attempt(field="arguments", pattern=";", source_ip="10.0.0.1")
handler.log_rate_limit_exceeded(provider_id="test", limit=100, window_seconds=60)
```

### Event Types

| Event Type | Severity |
|------------|----------|
| `ACCESS_GRANTED` | INFO |
| `ACCESS_DENIED` | LOW |
| `RATE_LIMIT_EXCEEDED` | MEDIUM |
| `VALIDATION_FAILED` | LOW-MEDIUM |
| `INJECTION_ATTEMPT` | HIGH |
| `SUSPICIOUS_COMMAND` | HIGH |
| `PROVIDER_COMPROMISE_SUSPECTED` | HIGH |

## Configuration

```yaml
providers:
  math_provider:
    mode: subprocess
    command:
      - python3
      - -m
      - examples.provider_math.server
    idle_ttl_s: 180

  docker_provider:
    mode: docker
    image: ghcr.io/myorg/mcp-provider:v1.0
    idle_ttl_s: 300
```

## Best Practices

1. **Use subprocess mode** for trusted local providers
2. **Use container mode** with security restrictions for untrusted code
3. **Enable rate limiting** in production
4. **Never log sensitive values** - use SecureEnvironment
5. **Monitor HIGH severity events** and set up alerts
6. **Container security**: minimal base images, non-root user, read-only filesystem, disable network when not needed

## Testing

```bash
pytest tests/unit/test_security.py -v
pytest -m security -v
```

## Incident Response

Monitor for:
- High rate of `INJECTION_ATTEMPT` events
- Repeated `VALIDATION_FAILED` from same source
- `PROVIDER_COMPROMISE_SUSPECTED` events
- Unusual `RATE_LIMIT_EXCEEDED` patterns

Response steps:
1. Block suspicious sources
2. Review security audit logs
3. Update blocklists, tighten validation
4. Document incident
5. Improve security measures

## Reporting Vulnerabilities

Do NOT create public GitHub issues for security vulnerabilities. See [SECURITY.md](../SECURITY.md) in the project root for reporting instructions.
