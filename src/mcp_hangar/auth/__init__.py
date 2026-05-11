"""Enterprise authentication and authorization module.

Provides:
- RBAC (role-based access control)
- API key authentication with rotation
- JWT/OIDC integration (Keycloak, Entra ID, Okta)
- Tool Access Policies (glob-pattern allow/deny)
- Auth REST API endpoints
- Auth CLI management commands
"""

from mcp_hangar.auth.bootstrap import AuthComponents, NullAuthComponents, bootstrap_auth
from mcp_hangar.auth.config import parse_auth_config

__all__ = [
    "AuthComponents",
    "NullAuthComponents",
    "bootstrap_auth",
    "parse_auth_config",
]
