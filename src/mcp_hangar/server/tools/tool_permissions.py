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
