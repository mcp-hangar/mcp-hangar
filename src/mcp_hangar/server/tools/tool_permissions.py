"""Declarative tool-to-permission table for the MCP control-plane surface (#909).

Why this exists
---------------
The REST API resolves authorization from the route
(:mod:`mcp_hangar.server.api.route_permissions`), fail-closed, since 2.2.0. The
MCP tool surface got none of that. ``hangar_call`` authorizes every call it
dispatches -- ``_authorize_calls`` checks ``tool:invoke`` per call -- and the
other twenty-one ``hangar_*`` tools authorized nothing at all.

So with auth on, one identity in one process was refused
``POST /api/mcp_servers/{id}/stop`` and accepted on ``hangar_stop`` over MCP.
Authentication was enforced on both; authorization on one. That is the same
failure the route table was written to end, reached through the other door.

The same decision is taken here, for the same reason: authorization is resolved
from the **tool name**, not from the tool body, and a name absent from this
table is DENIED. A tool added without an entry is unreachable rather than
public, and ``tests/unit/test_tool_permissions_cover_the_surface.py`` fails the
build when one appears -- because "the author remembers to add a guard" is the
assumption that produced this issue.

Permission choices
------------------
Each entry mirrors what the REST route performing the same operation already
requires, so no role migration is needed and no permission is invented:

* lifecycle operations (``hangar_start`` / ``hangar_stop`` / ``hangar_warm``)
  take ``mcp_servers:lifecycle``, as ``/mcp_servers/{id}/start`` does.
* ``hangar_load`` / ``hangar_unload`` add and remove a server, so they take
  ``mcp_servers:write`` like ``POST`` and ``DELETE /mcp_servers``. This is what
  the ``provider:load`` / ``provider:unload`` entries in ``UNENFORCED_BY_DESIGN``
  were standing in for; those are legacy vocabulary and stay deleted rather than
  wired up.
* ``hangar_reload_config`` takes ``config:reload``, admin-only, because reload
  re-applies every governance input -- tool-access policies, digest pins,
  topology mode -- and launches servers.
* the discovery tools split the way the discovery routes do: reads are
  ``discovery:read``, a scan is ``discovery:trigger``, and approving or
  quarantining is ``discovery:approve``.
* the continuation tools take ``tool:invoke``. They hand back the truncated tail
  of a tool result, so whoever may invoke a tool may read the rest of what it
  returned.
"""

from __future__ import annotations

from typing import Any

from ...logging_config import get_logger

logger = get_logger(__name__)

#: Tool name -> (resource_type, action) required to call it.
TOOL_PERMISSIONS: dict[str, tuple[str, str]] = {
    # --- Fleet reads ---------------------------------------------------------
    "hangar_list": ("mcp_servers", "read"),
    "hangar_status": ("mcp_servers", "read"),
    "hangar_details": ("mcp_servers", "read"),
    "hangar_tools": ("mcp_servers", "read"),
    "hangar_health": ("mcp_servers", "read"),
    # --- Fleet lifecycle -----------------------------------------------------
    "hangar_start": ("mcp_servers", "lifecycle"),
    "hangar_stop": ("mcp_servers", "lifecycle"),
    "hangar_warm": ("mcp_servers", "lifecycle"),
    # --- Fleet membership ----------------------------------------------------
    "hangar_load": ("mcp_servers", "write"),
    "hangar_unload": ("mcp_servers", "write"),
    # --- Configuration -------------------------------------------------------
    "hangar_reload_config": ("config", "reload"),
    # --- Discovery -----------------------------------------------------------
    "hangar_discover": ("discovery", "trigger"),
    "hangar_discovered": ("discovery", "read"),
    "hangar_sources": ("discovery", "read"),
    "hangar_approve": ("discovery", "approve"),
    "hangar_quarantine": ("discovery", "approve"),
    # --- Groups --------------------------------------------------------------
    "hangar_group_list": ("group", "read"),
    "hangar_group_rebalance": ("group", "update"),
    # --- Metrics -------------------------------------------------------------
    # Distinct from the `/metrics` scrape endpoint, which is deliberately
    # unauthenticated (it is on the auth skip list). This tool is reached
    # through the authenticated MCP surface, so the permission applies.
    "hangar_metrics": ("metrics", "read"),
    # --- Continuations -------------------------------------------------------
    "hangar_fetch_continuation": ("tool", "invoke"),
    "hangar_delete_continuation": ("tool", "invoke"),
}

#: Tools that authorize themselves and must NOT be gated by the table.
#:
#: ``hangar_call`` checks ``tool:invoke`` for each call in the batch
#: (``_authorize_calls``), which is finer than anything a single entry here
#: could express: one batch may carry calls the principal may make and calls it
#: may not, and the per-call check denies only the latter. A second, coarser
#: check in front of it would be a different rule that could drift from that
#: one.
SELF_AUTHORIZING_TOOLS: frozenset[str] = frozenset({"hangar_call"})

#: Tools that belong to the invoke path rather than the control plane (#904).
#:
#: The front-door management surface is "what an operator uses to run the
#: gateway", and these are not that. `hangar_call` is the `egress` way to invoke
#: a tool; the continuation tools hand back the tail of a truncated *tool result*
#: and are reached by whoever made the call, not by whoever administers the
#: fleet. Projecting them as management would put invoke-path plumbing in an
#: operator's list and still leave it out of an agent's, which is backwards.
#:
#: (A front-door client that receives a truncated result has no way to fetch the
#: rest today, because the flat surface projects neither. That gap predates this
#: and is not closed here.)
INVOKE_PATH_TOOLS: frozenset[str] = SELF_AUTHORIZING_TOOLS | frozenset(
    {"hangar_fetch_continuation", "hangar_delete_continuation"}
)


def management_tools_for(mcp_ctx: Any) -> frozenset[str]:
    """Which management tools this caller may see, because it may call them (#904).

    The front door serves flat upstream names and no control plane, for everyone,
    which means an operator on a front-door gateway has no management surface
    over MCP at all -- and turning the mode off to get one hands every agent the
    whole meta-API. This is what makes the surface a property of the caller
    instead of the instance.

    The set is exactly what :func:`authorize_tool` would permit, so the list and
    the invoke agree by construction rather than by two rules kept in step. A
    tool shown here is callable; a tool not shown is refused if called anyway.

    **Stricter than :func:`authorize_tool` in one respect**, deliberately: that
    function allows everything when auth is off, which is the backward-compatible
    behaviour ``--unsafe-no-auth`` depends on. Projecting on the same rule would
    conjure a control-plane surface for an unauthenticated caller on a front door
    that today shows it nothing. So with no configured auth, or no authenticated
    principal, this is empty -- and the tools stay unreachable too, because the
    call path only dispatches what this returned.

    ``INVOKE_PATH_TOOLS`` are excluded. They are how a tool is called rather than
    how the gateway is administered, and on a front door the flat names already
    are the invoke path.

    Args:
        mcp_ctx: The MCP request Context, carrying the authenticated principal on
            ``request.state.auth``.

    Returns:
        The names this caller may both see and call. Empty when auth is off or
        the caller is anonymous.
    """
    try:
        from ..context import get_context

        auth_components = getattr(get_context(), "auth_components", None)
    except Exception:  # noqa: BLE001 -- no app context (stdio/local) -> no management surface
        return frozenset()

    authz = getattr(auth_components, "authz_middleware", None)
    if authz is None or not getattr(auth_components, "enabled", False):
        return frozenset()

    try:
        inner = getattr(mcp_ctx, "request_context", None) or mcp_ctx
        auth_state = getattr(getattr(inner, "request", None), "state", None)
        principal = getattr(getattr(auth_state, "auth", None), "principal", None)
    except Exception:  # noqa: BLE001 -- fault barrier: identity lookup must not break listing
        principal = None

    if principal is None or principal.is_anonymous():
        return frozenset()

    permitted: set[str] = set()
    for tool_name, (resource_type, action) in TOOL_PERMISSIONS.items():
        if tool_name in INVOKE_PATH_TOOLS:
            continue
        try:
            authz.authorize(
                principal=principal,
                action=action,
                resource_type=resource_type,
                resource_id="*",
            )
        except Exception:  # noqa: BLE001 -- a denial is the normal answer here, not an error
            continue
        permitted.add(tool_name)
    return frozenset(permitted)


class ToolAccessNotAuthorizedError(PermissionError):
    """The caller may not invoke this tool.

    ``PermissionError`` so it is distinguishable from the tool's own failures and
    is not swallowed by the value-error handling in the tool bodies.
    """


def authorize_tool(tool_name: str, mcp_ctx: Any) -> None:
    """Enforce the table for *tool_name*, fail-closed.

    Semantics deliberately identical to ``_authorize_calls`` on the
    ``hangar_call`` path, so the two cannot disagree about what "auth is off"
    means:

    * no resolvable authz middleware, or auth components present but disabled
      (stdio, local, ``--unsafe-no-auth``) -> allow. Turning this into a denial
      would make an auth-off gateway unusable, which is the regression #600
      corrected on the REST side.
    * auth configured and the principal missing or anonymous -> deny.
    * tool absent from the table and not self-authorizing -> deny. This is the
      fail-closed default that makes a forgotten entry safe.
    * otherwise the authorizer decides, and any error from it is a denial.

    Args:
        tool_name: The registered tool name being invoked.
        mcp_ctx: The MCP request Context the wrapper injected, carrying the
            authenticated principal on ``request.state.auth``.

    Raises:
        ToolAccessNotAuthorizedError: If the call is not authorized.
    """
    if tool_name in SELF_AUTHORIZING_TOOLS:
        return

    try:
        from ..context import get_context

        auth_components = getattr(get_context(), "auth_components", None)
    except Exception:  # noqa: BLE001 -- no app context (stdio/local) -> auth off, allow
        auth_components = None

    authz = getattr(auth_components, "authz_middleware", None)
    if authz is None or not getattr(auth_components, "enabled", False):
        return

    try:
        inner = getattr(mcp_ctx, "request_context", None) or mcp_ctx
        auth_state = getattr(getattr(inner, "request", None), "state", None)
        principal = getattr(getattr(auth_state, "auth", None), "principal", None)
    except Exception:  # noqa: BLE001 -- fault barrier: identity lookup must not crash the call
        principal = None

    if principal is None or principal.is_anonymous():
        logger.warning("tool_authorization_denied", tool=tool_name, reason="missing_credentials")
        raise ToolAccessNotAuthorizedError(f"Authentication required to call '{tool_name}'")

    permission = TOOL_PERMISSIONS.get(tool_name)
    if permission is None:
        # An unmapped tool is unreachable, not public. See the module docstring.
        logger.warning("tool_authorization_denied", tool=tool_name, reason="no_permission_mapped")
        raise ToolAccessNotAuthorizedError(
            f"'{tool_name}' declares no required permission and is refused. This is a defect in the "
            "tool, not in the request: add it to TOOL_PERMISSIONS."
        )

    resource_type, action = permission
    try:
        authz.authorize(
            principal=principal,
            action=action,
            resource_type=resource_type,
            resource_id="*",
        )
    except Exception as exc:  # noqa: BLE001 -- fail-closed: any denial or authorizer error refuses
        logger.warning("tool_authorization_denied", tool=tool_name, reason=type(exc).__name__)
        raise ToolAccessNotAuthorizedError(
            f"Not authorized to call '{tool_name}': {resource_type}:{action} permission required"
        ) from exc
