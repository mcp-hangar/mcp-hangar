# Authentication & Authorization

MCP-Hangar supports enterprise-grade authentication (AuthN) and authorization (AuthZ) for secure multi-tenant access control.

## Quick Start

Authentication is **opt-in** and disabled by default. To enable it:

### 1. Enable in Configuration

```yaml
# config.yaml
auth:
  enabled: true  # Enable authentication
  allow_anonymous: false  # Require authentication for all requests

  api_key:
    enabled: true
    header_name: X-API-Key
```

### 2. Create an API Key

```bash
# Via the REST API (there is no auth CLI subcommand)
curl -X POST http://localhost:8000/api/auth/keys \
  -H "X-API-Key: <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "principal": "service:my-app",
    "name": "My App Key",
    "role": "developer"
  }'

# Response:
# {
#   "key": "mcp_aBcDeFgHiJkLmNoPqRsTuVwXyZ...",
#   "principal": "service:my-app"
# }
# Save this key now - it cannot be retrieved later!
```

### 3. Use the API Key

```bash
# HTTP mode
curl -H "X-API-Key: mcp_aBcDeFgHiJkLmNoPqRsTuVwXyZ..." \
  http://localhost:8000/mcp

# Or in MCP client configuration
{
  "headers": {
    "X-API-Key": "mcp_aBcDeFgHiJkLmNoPqRsTuVwXyZ..."
  }
}
```

## Authentication Methods

### API Key Authentication

Simple key-based authentication. Keys are:

- Prefixed with `mcp_` for easy identification
- Stored as SHA-256 hashes (never in plaintext)
- Support expiration and revocation

```yaml
auth:
  api_key:
    enabled: true
    header_name: X-API-Key  # Can be customized
```

### JWT/OIDC Authentication

For enterprise SSO integration with MCP servers like Okta, Auth0, Azure AD:

```yaml
auth:
  oidc:
    enabled: true
    issuer: https://auth.company.com
    audience: mcp-hangar
    # Claim mappings (optional)
    groups_claim: groups
    tenant_claim: org_id
```

## Authorization (RBAC)

### Built-in Roles

| Role | Description | Permissions |
|------|-------------|-------------|
| `admin` | Full access | Everything |
| `mcp_server_admin` | Manage MCP servers | MCP server:*, tool:invoke, tool:list |
| `developer` | Use tools | MCP server:read/list, tool:invoke/list, MCP server:start |
| `viewer` | Read-only | MCP server:read/list, tool:list, metrics:read |
| `auditor` | Audit logs | audit:read, metrics:read |

### Assigning Roles

#### Static (in config.yaml)

```yaml
auth:
  role_assignments:
    - principal: "user:admin@company.com"
      role: admin
      scope: global

    - principal: "group:developers"
      role: developer
      scope: global

    # Tenant-scoped assignment
    - principal: "group:data-team"
      role: developer
      scope: "tenant:data-team"
```

#### Dynamic (via REST API)

```bash
curl -X POST http://localhost:8000/api/auth/roles/assign \
  -H "X-API-Key: <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "principal": "user:john@company.com",
    "role": "developer",
    "scope": "global"
  }'
```

## Security Best Practices

### 1. Use HTTPS in Production

Always use HTTPS for MCP endpoints in production. The auth system will warn if OIDC issuer is not HTTPS.

### 2. Configure Trusted Proxies

If behind a load balancer, configure trusted proxies for correct client IP detection.
Trusted proxies are set programmatically via `FastMCPServerConfig`:

```python
from mcp_hangar.fastmcp_server.config import FastMCPServerConfig

config = FastMCPServerConfig(
    trusted_proxies=frozenset(["10.0.0.0/8", "172.16.0.0/12"]),
)
```

### 3. Rotate API Keys Regularly

Set expiration for API keys and rotate them periodically:

```bash
curl -X POST http://localhost:8000/api/auth/keys \
  -H "X-API-Key: <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "principal": "service:ci",
    "name": "CI Pipeline Key",
    "expires_in_days": 30
  }'
```

### 4. Use Tenant Isolation

For multi-tenant deployments, use tenant-scoped roles:

```yaml
role_assignments:
  - principal: "group:team-alpha"
    role: developer
    scope: "tenant:alpha"
```

## Monitoring

Auth events are emitted as domain events and can be monitored:

- `AuthenticationSucceeded` - Successful authentication
- `AuthenticationFailed` - Failed authentication attempt
- `AuthorizationDenied` - Access denied
- `AuthorizationGranted` - Access granted

These are logged and can be sent to your observability stack.

## Troubleshooting

### "No valid credentials provided"

- Check that `auth.enabled: true` is set
- Verify the X-API-Key header is being sent
- Ensure the key has the correct prefix (`mcp_`)

### "Invalid API key"

- The key may have been revoked
- The key may have expired
- Check for typos in the key

### "Access denied"

- The principal doesn't have the required role
- Check role assignments via the REST API (`GET /api/auth/roles`)
- Verify the scope matches

## API Reference

### Configuration Schema

```yaml
auth:
  enabled: bool          # Master switch (default: false)
  allow_anonymous: bool  # Allow unauthenticated requests (default: false)

  api_key:
    enabled: bool        # Enable API key auth (default: true when auth enabled)
    header_name: str     # Header name (default: X-API-Key)

  oidc:
    enabled: bool        # Enable OIDC/JWT auth (default: false)
    issuer: str          # OIDC issuer URL
    audience: str        # Expected audience claim
    jwks_uri: str        # JWKS endpoint (auto-discovered if not set)
    subject_claim: str   # JWT claim for subject (default: sub)
    groups_claim: str    # JWT claim for groups (default: groups)
    tenant_claim: str    # JWT claim for tenant (default: tenant_id)

  opa:
    enabled: bool        # Enable OPA policy engine (default: false)
    url: str             # OPA server URL
    policy_path: str     # Policy decision path

  role_assignments: []   # Static role assignments
```
