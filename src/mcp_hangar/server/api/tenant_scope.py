"""The tenant a REST or WebSocket request is confined to.

The route guard (``AuthorizationEnforcementMiddleware``) decides it once, from
the same authorization call that let the request in, and records it on the ASGI
scope. A handler behind a ``tenant_aware`` rule reads it here and serves or
changes nothing outside that tenant. Every other rule never gets this far with a
tenant-scoped grant, because the guard refuses one.

Reading the guard's record, rather than authorizing again, keeps a single
decision. The handler cannot come to a different conclusion than the guard
because it consulted different auth components -- the drift that
``AuthorizationEnforcementMiddleware`` binds its components to the router to
avoid.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import HTTPConnection
from starlette.types import Scope

from ...domain.contracts.authorization import GrantScope
from ...domain.exceptions import AccessDeniedError
from ..context import get_context

_STATE_KEY = "authz_grant_scope"


def record_grant_scope(scope: Scope, grant: GrantScope) -> None:
    """Record the guard's decision on *scope* for the handler to read.

    The scope's ``state`` comes in two shapes -- a ``State`` set by the outer
    authentication middleware, or the plain ``dict`` Starlette's
    ``Request.state`` wraps -- and both are written the way they are read.
    """
    state = scope.get("state")
    if state is None:
        state = {}
        scope["state"] = state
    if isinstance(state, dict):
        state[_STATE_KEY] = grant
    else:
        setattr(state, _STATE_KEY, grant)


def _state_value(scope: Any, key: str) -> Any:
    state = scope.get("state") if isinstance(scope, dict) else None
    if state is None:
        return None
    return state.get(key) if isinstance(state, dict) else getattr(state, key, None)


def _principal_id(conn: HTTPConnection) -> str:
    principal = getattr(_state_value(conn.scope, "auth"), "principal", None)
    return str(getattr(principal, "id", "unknown"))


def out_of_scope(conn: HTTPConnection, *, action: str, resource: str, reason: str) -> AccessDeniedError:
    """The refusal a tenant-aware handler raises for a request outside its tenant."""
    return AccessDeniedError(principal_id=_principal_id(conn), action=action, resource=resource, reason=reason)


def confined_tenant(conn: HTTPConnection) -> str | None:
    """The tenant this request may act on, or None when it reaches the whole fleet.

    None means a global grant, an authorizer that binds no scopes, or auth off.
    A string is the tenant a tenant-scoped grant is limited to.

    A handler reached without the guard having recorded anything -- driven
    directly rather than through ``create_api_router`` -- falls back to the
    application context. With auth off there, nothing is narrowed, as
    everywhere else. With auth on, the request is refused: a missing decision is
    not a global one.

    Raises:
        AccessDeniedError: The decision is missing while auth is on, or the
            grant's scope is not one this code recognises.
    """
    recorded = _state_value(getattr(conn, "scope", None), _STATE_KEY)
    grant = recorded if isinstance(recorded, GrantScope) else _unguarded(conn)
    if grant.confined and grant.tenant_id is None:
        raise out_of_scope(conn, action="access", resource=str(conn.url.path), reason="unrecognised grant scope")
    return grant.tenant_id


def _unguarded(conn: HTTPConnection) -> GrantScope:
    """No decision was recorded: only auth off reads as unconfined."""
    try:
        auth_components = getattr(get_context(), "auth_components", None)
    except Exception as exc:  # noqa: BLE001 -- fail-closed: an unreadable context is not "auth off"
        raise out_of_scope(
            conn, action="access", resource=str(conn.url.path), reason="no authorization decision recorded"
        ) from exc
    if getattr(auth_components, "authz_middleware", None) is None or not getattr(auth_components, "enabled", False):
        return GrantScope()
    raise out_of_scope(conn, action="access", resource=str(conn.url.path), reason="no authorization decision recorded")
