"""Enterprise authentication and authorization module.

Licensed under the Business Source License 1.1.
See enterprise/LICENSE.BSL for full terms.

Provides:
- RBAC (role-based access control)
- API key authentication with rotation
- JWT/OIDC integration (Keycloak, Entra ID, Okta)
- Tool Access Policies (glob-pattern allow/deny)
- Auth REST API endpoints
- Auth CLI management commands
"""

from enterprise.auth.bootstrap import AuthComponents, NullAuthComponents, bootstrap_auth
from enterprise.auth.config import parse_auth_config

__all__ = [
    "AuthComponents",
    "NullAuthComponents",
    "bootstrap_auth",
    "parse_auth_config",
]
