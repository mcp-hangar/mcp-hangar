# MCP-Hangar + Keycloak Integration Test

This example demonstrates how to integrate MCP-Hangar with Keycloak for OIDC/JWT authentication.

## Quick Start

### 1. Start the services

```bash
cd examples/auth-keycloak
docker-compose up -d
```

This starts:
- **Keycloak** on http://localhost:8080
- **MCP-Hangar** on http://localhost:9000 (with auth enabled)

### 2. Wait for services to be ready

```bash
# Wait for Keycloak
until curl -sf http://localhost:8080/health/ready; do sleep 2; done

# Wait for MCP-Hangar
until curl -sf http://localhost:9000/health/live; do sleep 2; done
```

### 3. Run the test script

```bash
chmod +x test-auth.sh
./test-auth.sh
```

## Test Users

The Keycloak realm comes pre-configured with test users:

| Username | Password | Role | Group |
|----------|----------|------|-------|
| admin | admin123 | admin | platform-engineering |
| developer | dev123 | developer | developers |
| viewer | view123 | viewer | viewers |

## Manual Testing

### Get an access token from Keycloak

```bash
# Get token for admin user
TOKEN=$(curl -s -X POST "http://localhost:8080/realms/mcp-hangar/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=password" \
  -d "client_id=mcp-cli" \
  -d "username=admin" \
  -d "password=admin123" \
  | jq -r '.access_token')

echo "Token: $TOKEN"
```

### Call MCP-Hangar with the token

```bash
# List providers (requires viewer role or higher)
curl -H "Authorization: Bearer $TOKEN" http://localhost:9000/mcp

# Without auth (should fail with 401)
curl http://localhost:9000/mcp
```

### Decode the JWT to see claims

```bash
echo "$TOKEN" | cut -d'.' -f2 | base64 -d | jq .
```

Example claims:
```json
{
  "sub": "12345-abcde-...",
  "email": "admin@example.com",
  "groups": ["platform-engineering"],
  "roles": ["admin"],
  ...
}
```

## Keycloak Admin Console

Access the Keycloak admin console at:
- URL: http://localhost:8080/admin
- Username: `admin`
- Password: `admin`

From here you can:
- Create/modify users
- Manage groups and roles
- Configure client settings
- View login events

## Configuration Details

### MCP-Hangar Auth Config (`config.yaml`)

```yaml
auth:
  enabled: true
  allow_anonymous: false

  oidc:
    enabled: true
    issuer: http://keycloak:8080/realms/mcp-hangar
    audience: mcp-hangar
    groups_claim: groups

  role_assignments:
    - principal: "group:platform-engineering"
      role: admin
      scope: global
    - principal: "group:developers"
      role: developer
      scope: global
```

### Keycloak Client Settings

The `mcp-hangar` client in Keycloak is configured with:
- Protocol mappers to include `groups` and `roles` in JWT
- Direct access grants enabled (for password flow)
- Client secret: `mcp-hangar-secret`

## Cleanup

```bash
docker-compose down -v
```

## Troubleshooting

### "Connection refused" to Keycloak

Keycloak takes 30-60 seconds to start. Wait for the health check to pass.

### "Invalid token" errors

- Check that the issuer URL matches exactly (including trailing slash)
- Verify the audience matches the client_id
- Ensure the token hasn't expired

### Token not working

Decode the token and verify:
```bash
echo "$TOKEN" | cut -d'.' -f2 | base64 -d | jq .
```

Check that:
- `iss` matches your issuer URL
- `aud` contains your client_id
- `exp` is in the future
- `groups` claim is present
