"""``hangar_whoami``: the identity the gateway resolved for this caller (#1363).

Nothing else tells a caller who the gateway thinks it is; a refused call says
only that it was refused. This answers with the principal, tenant, groups and
role bindings the authorizer decides that caller on.

"The same view the authorizer used" is structural rather than parallel: the
caller and its authorizer come from :func:`tool_permissions.resolve_caller`,
which is how :func:`tool_permissions.authorize_tool` picks them, and the roles
from :meth:`RBACAuthorizer.role_bindings`, which is the lookup ``authorize``
itself runs. There is no parameter, so a caller can only ever ask about itself.
"""

from __future__ import annotations

from typing import Any

from mcp_hangar._sdk_compat import Context, FastMCP

from ...application.mcp.tooling import key_global, mcp_tool_wrapper
from ..validation import not_rate_limited, tool_error_hook, tool_error_mapper
from .tool_permissions import ToolAccessNotAuthorizedError, resolve_caller


def _role_view(authz: Any, principal: Any) -> tuple[list[dict[str, str]], str | None]:
    """The caller's ``(scope, role)`` bindings and how OPA can change their verdict.

    Fails closed: an authorizer this cannot see the roles of raises rather than
    reporting an empty list that would read as "no roles".
    """
    from ...auth.infrastructure.opa_authorizer import CombinedAuthorizer
    from ...auth.infrastructure.rbac_authorizer import RBACAuthorizer

    authorizer = getattr(authz, "authorizer", None)
    opa_effect = None
    if isinstance(authorizer, CombinedAuthorizer):
        opa_effect = authorizer.opa_effect
        authorizer = authorizer.rbac
    if not isinstance(authorizer, RBACAuthorizer):
        raise RuntimeError(f"the configured authorizer ({type(authorizer).__name__}) does not expose role bindings")

    bindings = authorizer.role_bindings(principal)
    held_globally = {role.name for scope, role in bindings if scope == "global"}
    roles: list[dict[str, str]] = []
    for scope, role in bindings:
        entry = {"role": role.name, "scope": scope}
        # A role held directly and through a group is one binding, and a global
        # one subsumes the same role within a tenant: global bindings are checked
        # first, so the tenant copy never decides anything. (The SQL stores
        # return global rows for a tenant-scope query too.)
        if entry in roles or (scope != "global" and role.name in held_globally):
            continue
        roles.append(entry)
    return roles, opa_effect


def hangar_whoami(ctx: Any) -> dict[str, Any]:
    """The caller's resolved identity, read from *ctx*'s request."""
    caller = resolve_caller(ctx)
    if caller is None:
        # The guard refused this already; kept so the body cannot answer for nobody.
        raise ToolAccessNotAuthorizedError("'hangar_whoami' answers only an authenticated caller")
    principal, authz = caller
    roles, opa_effect = _role_view(authz, principal)
    return {
        "principal": {"id": principal.id.value, "type": principal.type.value},
        "tenant_id": principal.tenant_id,
        "groups": sorted(principal.groups),
        "roles": roles,
        "opa_policy": opa_effect,
    }


def register_whoami_tools(mcp: FastMCP) -> None:
    """Register ``hangar_whoami``."""

    @mcp.tool(name="hangar_whoami")
    @mcp_tool_wrapper(
        tool_name="hangar_whoami",
        rate_limit_key=key_global,
        check_rate_limit=not_rate_limited,
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def _hangar_whoami(ctx: Context) -> dict:
        """Show who this gateway thinks you are: principal, tenant, groups and roles.

        CHOOSE THIS when: a call was refused and you need to know which identity
        and which role bindings the gateway judged it by.

        Reads only your own identity; there is no way to ask about another
        principal or tenant. Needs no permission, but needs an authenticated
        caller: with auth off, or anonymously, there is no identity and this tool
        is not offered.

        Side effects: None (read-only).

        Returns:
            {
                principal: {id: str, type: str},
                tenant_id: str | null,
                groups: [str],
                roles: [{role: str, scope: "global" | "tenant:<id>"}],
                opa_policy: null | "may_admit_more" | "may_refuse_more"
            }
            roles are the bindings the authorizer decides you on. A role bound at
            tenant:<id> grants only within that tenant, so it does not admit a
            management tool, which acts on the whole fleet. opa_policy is set
            when an OPA policy can change what those roles decide.

        Example:
            hangar_whoami()
            # {"principal": {"id": "svc:ci", "type": "service_account"}, "tenant_id": "A",
            #  "groups": [], "roles": [{"role": "developer", "scope": "tenant:A"}], "opa_policy": null}
        """
        return hangar_whoami(ctx)
