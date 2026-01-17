#!/usr/bin/env python3
"""Test Keycloak + MCP-Hangar OIDC integration."""
import base64
import json

import httpx

KEYCLOAK_URL = "http://localhost:8080"
REALM = "mcp-hangar"
CLIENT_ID = "mcp-cli"


def get_token(username: str, password: str) -> dict:
    """Get access token from Keycloak."""
    response = httpx.post(
        f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "username": username,
            "password": password,
        },
    )
    if response.status_code != 200:
        raise Exception(f"Failed: {response.status_code} - {response.text}")
    return response.json()


def decode_jwt(token: str) -> dict:
    """Decode JWT payload."""
    payload = token.split(".")[1]
    padding = 4 - len(payload) % 4
    if padding != 4:
        payload += "=" * padding
    return json.loads(base64.urlsafe_b64decode(payload))


print("=" * 60)
print("Keycloak + MCP-Hangar OIDC Integration Test")
print("=" * 60)

# Test users
users = [
    ("admin", "admin123"),
    ("developer", "dev123"),
    ("viewer", "view123"),
]

for username, password in users:
    print(f"\n--- Testing user: {username} ---")
    try:
        token_data = get_token(username, password)
        access_token = token_data["access_token"]
        print(f"✓ Got token (expires in {token_data.get('expires_in')}s)")

        claims = decode_jwt(access_token)
        print(f"  Subject: {claims.get('sub')}")
        print(f"  Email: {claims.get('email')}")
        print(f"  Preferred username: {claims.get('preferred_username')}")
        print(f"  Groups: {claims.get('groups', [])}")
        print(f"  Realm roles: {claims.get('realm_access', {}).get('roles', [])}")
        print(f"  Issuer: {claims.get('iss')}")

    except Exception as e:
        print(f"✗ Error: {e}")

print("\n" + "=" * 60)
print("Now testing with MCP-Hangar JWT authenticator...")
print("=" * 60)

from mcp_hangar.domain.contracts.authentication import AuthRequest

# Test with MCP-Hangar
from mcp_hangar.infrastructure.auth.jwt_authenticator import JWKSTokenValidator, JWTAuthenticator, OIDCConfig

# Get admin token
token_data = get_token("admin", "admin123")
access_token = token_data["access_token"]

# Configure OIDC - audience is now "mcp-hangar" thanks to our audience mapper
config = OIDCConfig(
    issuer="http://localhost:8080/realms/mcp-hangar",
    audience="mcp-hangar",  # Our client_id - added by audience mapper
    groups_claim="groups",
)

validator = JWKSTokenValidator(config)
authenticator = JWTAuthenticator(config, validator)

request = AuthRequest(
    headers={"Authorization": f"Bearer {access_token}"},
    source_ip="127.0.0.1",
    method="GET",
    path="/mcp",
)

print("\nAuthenticating with JWT...")
try:
    principal = authenticator.authenticate(request)
    print("✓ SUCCESS!")
    print(f"  Principal ID: {principal.id.value}")
    print(f"  Type: {principal.type.value}")
    print(f"  Groups: {list(principal.groups)}")
    print(f"  Tenant: {principal.tenant_id}")
    print(f"  Metadata: {principal.metadata}")
except Exception as e:
    print(f"✗ Authentication failed: {e}")
    print("  (This might be due to audience mismatch)")

print("\n" + "=" * 60)
print("Integration test complete!")
print("=" * 60)

# Full authorization test
print("\n" + "=" * 60)
print("Testing full AuthN + AuthZ flow...")
print("=" * 60)

from mcp_hangar.server.auth_bootstrap import bootstrap_auth
from mcp_hangar.server.auth_config import AuthConfig, OIDCAuthConfig

# Create auth config matching Keycloak
auth_config = AuthConfig(
    enabled=True,
    allow_anonymous=False,
    oidc=OIDCAuthConfig(
        enabled=True,
        issuer="http://localhost:8080/realms/mcp-hangar",
        audience="mcp-hangar",
        groups_claim="groups",
    ),
)

# Bootstrap auth
auth_components = bootstrap_auth(auth_config)

# Map Keycloak groups to MCP-Hangar roles
auth_components.role_store.assign_role("group:platform-engineering", "admin")
auth_components.role_store.assign_role("group:developers", "developer")
auth_components.role_store.assign_role("group:viewers", "viewer")

print("\nTesting authorization for each user:")

test_cases = [
    ("admin", "admin123", "invoke", "tool", "math:add", True),
    ("admin", "admin123", "delete", "provider", "math", True),
    ("developer", "dev123", "invoke", "tool", "math:add", True),
    ("developer", "dev123", "delete", "provider", "math", False),
    ("viewer", "view123", "read", "provider", "math", True),
    ("viewer", "view123", "invoke", "tool", "math:add", False),
]

for username, password, action, resource_type, resource_id, expected in test_cases:
    # Get token
    token_data = get_token(username, password)
    access_token = token_data["access_token"]

    # Authenticate
    request = AuthRequest(
        headers={"Authorization": f"Bearer {access_token}"},
        source_ip="127.0.0.1",
        method="GET",
        path="/mcp",
    )

    auth_ctx = auth_components.authn_middleware.authenticate(request)

    # Authorize
    result = auth_components.authz_middleware.check(
        principal=auth_ctx.principal,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
    )

    status = "✓" if result == expected else "✗"
    expected_str = "allowed" if expected else "denied"
    actual_str = "allowed" if result else "denied"

    print(
        f"  {status} {username}: {action} on {resource_type}:{resource_id} -> {actual_str} (expected: {expected_str})"
    )

print("\n" + "=" * 60)
print("Full integration test complete!")
print("=" * 60)
