# Authentication Security Audit

Last audit date: 2026-03-08 (v0.11.0)

## Scope

This audit covers the authentication and authorization subsystem introduced in MCP Hangar:

- API key generation, storage, and validation
- JWT token issuance and verification
- Role-based access control (RBAC)
- Tool access policies (TAP)
- HTTP middleware enforcement

## API Key Security

### Key Generation

- Keys use `secrets.token_urlsafe(32)` (256 bits of entropy).
- Keys are prefixed with `mcp_` for identification in log scanning.
- Raw key is returned exactly once at creation time; only the SHA-256 hash is stored.

### Key Storage

- **SQLite backend**: Keys stored as SHA-256 hashes in `api_keys` table.
- **In-memory backend**: Keys stored as SHA-256 hashes in a dict (development only).
- Lookup by hash is constant-time via dict/index; no timing side-channel on comparison.

### Key Revocation

- Revoked keys are marked with `revoked_at` timestamp and `revoked_by` principal.
- Revocation is immediate; no grace period.
- Revoked keys are retained for audit trail; they never validate again.

## JWT Security

- Tokens signed with HS256 using a server-generated secret.
- Token lifetime defaults to 3600 seconds (configurable via `auth.jwt.expiry_s`).
- Refresh tokens are not supported; clients must re-authenticate.
- The signing secret is derived from `MCP_AUTH_SECRET` environment variable or auto-generated at startup (not persisted across restarts in auto-generated mode).

## RBAC Model

### Built-in Roles

| Role | Permissions |
|------|------------|
| `admin` | Full access to all operations |
| `operator` | Start, stop, reload providers; manage groups |
| `developer` | Invoke tools, read provider status |
| `viewer` | Read-only access to status and metrics |

### Custom Roles

- Custom roles extend built-in permissions with additional grants.
- Role assignments are stored in `IRoleStore` (SQLite or in-memory).
- A principal can have multiple roles; effective permissions are the union of all role grants.

## Tool Access Policies

- Policies are evaluated after RBAC permission check.
- A policy maps `(principal, provider, tool)` to `allow` or `deny`.
- Default policy when no TAP exists: `allow` (open by default).
- Policies are stored in `SQLiteToolAccessPolicyStore`.

## Middleware Enforcement

The `AuthMiddleware` in `http_auth_middleware.py`:

1. Extracts API key from `X-API-Key` header.
2. Validates key hash against store.
3. Resolves principal and roles.
4. Attaches `Principal` to request state.
5. Returns `401 Unauthorized` for invalid/missing keys (when `allow_anonymous: false`).
6. Returns `403 Forbidden` for insufficient permissions.

## Recommendations

| Item | Status | Notes |
|------|--------|-------|
| Key entropy (256-bit) | Pass | `secrets.token_urlsafe(32)` |
| Hash-only storage | Pass | Raw key never persisted |
| Timing-safe comparison | Pass | Dict lookup by hash |
| Rate limiting on auth endpoints | Pass | `RateLimitMiddleware` applied |
| Audit logging of auth events | Pass | `SecurityEventHandler` captures all auth events |
| TLS enforcement | Manual | Not enforced by Hangar; must be configured at reverse proxy level |
| Secret rotation | Manual | Requires restart; no online rotation mechanism yet |

## Open Items

- **mTLS for provider-to-Hangar communication**: Not yet implemented. Recommended for cross-network deployments.
- **OIDC/OAuth2 integration**: Planned for future release. Currently API key only.
- **Secret auto-rotation**: Signing key rotation without restart.
