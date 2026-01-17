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
# Using CLI (once implemented)
mcp-hangar auth create-key \
  --principal "service:my-app" \
  --name "My App Key" \
  --role developer

# Output:
# API Key created for service:my-app
# Key: mcp_aBcDeFgHiJkLmNoPqRsTuVwXyZ...
# ⚠️  Save this key now - it cannot be retrieved later!
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

For enterprise SSO integration with providers like Okta, Auth0, Azure AD:

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
| `provider-admin` | Manage providers | provider:*, tool:invoke, tool:list |
| `developer` | Use tools | provider:read/list, tool:invoke/list, provider:start |
| `viewer` | Read-only | provider:read/list, tool:list, metrics:read |
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

#### Dynamic (via CLI)

```bash
mcp-hangar auth assign-role \
  --principal "user:john@company.com" \
  --role developer \
  --scope global
```

## Security Best Practices

### 1. Use HTTPS in Production

Always use HTTPS for MCP endpoints in production. The auth system will warn if OIDC issuer is not HTTPS.

### 2. Configure Trusted Proxies

If behind a load balancer, configure trusted proxies for correct client IP detection:

```yaml
# In ServerConfig or via builder
trusted_proxies: ["10.0.0.0/8", "172.16.0.0/12"]
```

### 3. Rotate API Keys Regularly

Set expiration for API keys and rotate them periodically:

```bash
mcp-hangar auth create-key \
  --principal "service:ci" \
  --name "CI Pipeline Key" \
  --expires 30  # Expires in 30 days
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
- Check role assignments with `mcp-hangar auth list-roles`
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
