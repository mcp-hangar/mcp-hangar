# 12 -- Auth & RBAC

> **Prerequisite:** [01 -- HTTP Gateway](01-http-gateway.md)
> **You will need:** Running Hangar in HTTP mode
> **Time:** 10 minutes
> **Adds:** API key authentication and role-based access control

## The Problem

Your Hangar instance is accessible to everyone on the network. You need to control who can invoke tools, start providers, or manage configuration. Different teams need different access levels.

## The Config

```yaml
# config.yaml -- Recipe 12: Auth & RBAC
providers:
  my-mcp:
    mode: remote
    endpoint: "http://localhost:8080"
    health_check_interval_s: 10

auth:                                    # NEW: authentication config
  enabled: true                          # NEW: enable auth
  allow_anonymous: false                 # NEW: require auth for all requests

  api_key:                               # NEW: API key config
    enabled: true                        # NEW: enable API key auth
    header_name: X-API-Key               # NEW: header to read key from
```

## Try It

1. Start Hangar:

   ```bash
   mcp-hangar serve --http --port 8000
   ```

2. Try an unauthenticated request -- it fails:

   ```bash
   curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/providers
   ```

   ```
   401
   ```

3. Create an API key:

   ```bash
   curl -X POST http://localhost:8000/api/auth/keys \
     -H "Content-Type: application/json" \
     -d '{"principal_id": "service:my-app", "name": "My App Key"}'
   ```

   ```json
   {"key_id": "...", "raw_key": "mcp_aBcDeFg...", "principal_id": "service:my-app", "name": "My App Key"}
   ```

   Save the `raw_key` -- it is shown only once.

4. Use the key:

   ```bash
   curl -H "X-API-Key: mcp_aBcDeFg..." http://localhost:8000/api/providers
   ```

   ```json
   {"providers": [...]}
   ```

5. Assign a role:

   ```bash
   curl -X POST http://localhost:8000/api/auth/principals/service:my-app/roles \
     -H "X-API-Key: mcp_admin_key..." \
     -H "Content-Type: application/json" \
     -d '{"role_id": "developer"}'
   ```

6. Set a tool access policy:

   ```bash
   curl -X PUT http://localhost:8000/api/auth/policies/my-mcp/dangerous-tool \
     -H "X-API-Key: mcp_admin_key..." \
     -H "Content-Type: application/json" \
     -d '{"principal_id": "service:my-app", "effect": "deny"}'
   ```

## What Just Happened

Enabling auth adds the `AuthMiddleware` to the HTTP stack. Every request must include a valid API key in the `X-API-Key` header. The key is hashed and looked up in the auth store. The principal's roles determine what operations are allowed.

Built-in roles:

| Role | Can do |
|------|--------|
| `admin` | Everything |
| `operator` | Start, stop, reload, manage groups |
| `developer` | Invoke tools, read status |
| `viewer` | Read-only access |

Tool access policies add fine-grained control per (principal, provider, tool) tuple.

## Key Config Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `auth.enabled` | bool | `false` | Enable authentication |
| `auth.allow_anonymous` | bool | `true` | Allow unauthenticated requests |
| `auth.api_key.enabled` | bool | `true` | Enable API key authentication |
| `auth.api_key.header_name` | string | `X-API-Key` | HTTP header for API key |

## What's Next

You've secured access. Before going to production, run through the full checklist.

--> [13 -- Production Checklist](13-production-checklist.md)
