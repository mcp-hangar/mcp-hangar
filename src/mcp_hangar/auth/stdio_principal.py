"""The declared principal for a stdio session (ADR-026).

Identity reaches Hangar through ASGI middleware, which a stdio process never
enters: ``run_stdio`` serves a pipe, so there is no scope, no headers and no
request. Every caller was therefore anonymous, and ``front_door`` -- fail-closed
on identity since #902 -- projected zero tools to the one transport a laptop
uses.

ADR-026 makes the spawning process the trust boundary: the OS user launched it,
and ``auth.stdio.principal`` names the caller that implies. This module holds
that principal for the life of the process, because a stdio server serves
exactly one session over exactly one pair of pipes -- there is no second caller
for a per-request binding to distinguish.

Nothing here authenticates. The declaration is trusted because the spawn already
happened; what the principal may *do* is still resolved by the ordinary
authorization path from its roles.
"""

from __future__ import annotations

from typing import Any

from mcp_hangar.context import set_fallback_identity
from mcp_hangar.domain.value_objects import Principal
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext

_principal: Principal | None = None
_identity: IdentityContext | None = None
_authorizer: Any = None


def set_stdio_principal(principal: Principal) -> None:
    """Declare the principal for this stdio process.

    Called once during bootstrap, only when the serving transport is stdio and
    the configuration carries `auth.stdio.principal`.
    """
    global _principal, _identity, _authorizer
    _principal = principal
    _authorizer = _middleware_over_declared_roles(principal)
    _identity = IdentityContext(
        caller=CallerIdentity(
            user_id=principal.id.value,
            agent_id=None,
            # A pipe carries no bearer token and no proxy header, the two
            # sources a session id may come from, so a stdio caller has none
            # and a session suspension cannot match it (GHSA-fhwh-fmq2-7m5c).
            session_id=None,
            principal_type="user",
            tenant_id=principal.tenant_id,
        )
    )
    set_fallback_identity(_identity)


def clear_stdio_principal() -> None:
    """Forget the declared principal. For tests and for a re-bootstrap."""
    global _principal, _identity, _authorizer
    _principal = None
    _identity = None
    _authorizer = None
    set_fallback_identity(None)


def get_stdio_principal() -> Principal | None:
    """The declared principal, or None when no block was configured."""
    return _principal


def get_stdio_authorizer() -> Any:
    """The authorization middleware that decides for the declared principal, or None (#1622).

    The declaration is the principal's role source, as it is for the management
    surface: the configured role store is never written, so no HTTP principal
    with the same id can pick the declared roles up.
    """
    return _authorizer


def _middleware_over_declared_roles(principal: Principal) -> Any:
    """RBAC over a private in-memory store holding only the declared built-in roles."""
    from mcp_hangar.auth.infrastructure.middleware import AuthorizationMiddleware
    from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer
    from mcp_hangar.auth.roles import BUILTIN_ROLES

    store = InMemoryRoleStore()
    names = (principal.metadata or {}).get("roles", [])
    for name in names if isinstance(names, list) else []:
        if name in BUILTIN_ROLES:
            store.assign_role(principal.id.value, name)
    return AuthorizationMiddleware(authorizer=RBACAuthorizer(store))
