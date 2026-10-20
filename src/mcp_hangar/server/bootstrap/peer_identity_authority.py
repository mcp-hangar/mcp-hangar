"""The delivery layer's answer to `IPeerIdentityAuthority` (ADR-030 §9).

It decides exactly as the REST routes' `_check_permission` does, from the same
authorization components, read at call time because they are set up after the
command handlers are registered.
"""

from typing import Any

from ...domain.exceptions import AuthorizationError
from ...domain.value_objects.security import Principal
from ..context import get_context


class ContextPeerIdentityAuthority:
    """Whether a principal holds `mcp_servers:peer_identity`, under the gateway's authorizer."""

    def may_declare(self, principal: Principal | None) -> bool:
        auth_components: Any = getattr(get_context(), "auth_components", None)
        authz_middleware = getattr(auth_components, "authz_middleware", None)
        if authz_middleware is None or not getattr(auth_components, "enabled", False):
            # Authentication off allows every caller, as every REST route does.
            return True
        if principal is None or principal.is_anonymous():
            return False
        try:
            authz_middleware.authorize(
                principal=principal, action="peer_identity", resource_type="mcp_servers", resource_id="*"
            )
        except AuthorizationError:
            return False
        return True
