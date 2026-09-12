"""Declarative route-to-permission table for the REST/WebSocket API.

Why this exists
---------------
Authorization used to be a per-handler concern: each handler called
``_check_permission`` itself. That shape has two failure modes, and the codebase
hit both.

1. **A handler that forgets the call is silently public.** Only
   ``mcp_servers.py`` and ``admin_tools.py`` ever called the guard, so
   ``/config``, ``/discovery``, ``/groups``, ``/sessions``, ``/tools``, the whole
   ``/auth`` surface (API-key minting, role assignment, tool-access policy) and
   ``/approvals`` reads made no authorization decision at all. Authentication
   was enforced; authorization was not. Any principal holding any valid
   credential -- including the operator's ``X-API-Key`` -- could POST
   ``/api/auth/roles/assign`` and grant itself ``admin``.
2. **The guard gets copy-pasted and drifts.** The auth-off carve-out that #590
   and #600 corrected had to be re-stated in every module that re-implemented
   the check.

So the decision is inverted here: authorization is resolved from the route, not
from the handler, and the default for an unmatched route is DENY. A new route
is unreachable until it is named in this table -- fail-closed by construction,
which is what a policy enforcement plane has to mean for its own control API.

Reading the table
-----------------
Each rule is ``(path template, methods, permission)``, plus the
``tenant_aware`` flag described under "Tenant-scoped grants" below. ``permission`` is the
``(resource_type, action)`` pair passed to ``IAuthorizer.authorize``; ``None``
means the route requires a valid principal but no specific permission (used for
"describe myself" endpoints where the principal *is* the resource).

Rules are matched in order, so more specific templates must precede the general
ones they would otherwise be shadowed by (``/mcp_servers/{id}/l7_policy`` before
``/mcp_servers/{id}``).

Permission choices worth knowing
--------------------------------
* ``/mcp_servers/{id}/l7_policy`` requires ``policy:write``, not
  ``mcp_servers:write``. It is the operator's delivery channel for compiled
  ``MCPEgressPolicy`` objects (ADR-013); gating it on ``mcp_servers:write`` let
  ``ROLE_DEVELOPER`` clear an egress policy, because that role holds
  ``PERMISSION_PROVIDERS_WRITE``. ``policy:write`` is admin-only and was defined
  but never enforced anywhere before this table existed.
* ``/config/reload`` requires ``config:reload`` (admin-only). Reload re-applies
  every governance input -- tool-access policies, digest pins, topology mode --
  and launches MCP servers.
* ``/ws/events`` requires ``audit:read``. The socket streams *every* domain
  event, which is an audit-grade capability rather than a dashboard read.
* Everything under ``/auth`` requires ``("admin", "*")``. Only
  ``PERMISSION_ADMIN_ALL`` (``Permission("*", "*")``) can satisfy that pair, so
  the whole credential- and role-management surface is admin-only without
  inventing new permission constants that existing custom roles would not hold.

Tenant-scoped grants
--------------------
A role can be bound at ``tenant:<id>`` as well as ``global``, and a tenant
binding is a grant *within that tenant*. The permission check here is made
against ``resource_id="*"``, so on its own it cannot tell a tenant's viewer
from a fleet-wide one. The authorizer therefore reports the scope of the grant
that matched, and the guard honours it:

* **By default a rule requires a global grant.** A grant that only exists at
  tenant scope is refused (403, or close code 1008 on a websocket). Almost
  every route here serves or changes the whole fleet -- servers, groups,
  discovery, configuration, credentials -- and there is no tenant's share of
  that to hand out.
* **``tenant_aware=True`` opts a rule in**, and only a rule whose handler
  confines what it serves or changes to the grant's tenant may carry it. The
  handler reads that tenant from :func:`.tenant_scope.confined_tenant`,
  and withholds anything naming no tenant: that is fleet business. The approvals
  resolve handler applies the same rule itself. The flag is deliberately not
  inferred: it is a claim about the
  handler, and ``tests/unit/test_tenant_scoped_grants.py`` pins the exact set
  that makes it, so a new route cannot inherit tenant access by accident.

The permissions used here are deliberately restricted to constants that
``auth/roles.py`` already defines AND that a built-in role already grants, so
this change tightens enforcement without requiring a role migration. Where the
existing vocabulary is a loose fit (discovery source CRUD reuses
``discovery:approve``), that is called out in the rule comment rather than
papered over with a newly invented permission.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

# Sentinel for "authenticated principal required, no specific permission".
AUTHENTICATED_ONLY: None = None


@dataclass(frozen=True)
class RouteRule:
    """One row of the route-to-permission table."""

    pattern: re.Pattern[str]
    methods: frozenset[str] | None
    permission: tuple[str, str] | None
    template: str
    #: The handler confines what it serves or changes to the grant's tenant, so
    #: a tenant-scoped grant may pass. False: a global grant is required.
    tenant_aware: bool = False

    def matches(self, method: str, path: str) -> bool:
        if self.methods is not None and method.upper() not in self.methods:
            return False
        return self.pattern.fullmatch(path) is not None


def _rule(
    template: str,
    methods: str | None,
    permission: tuple[str, str] | None,
    *,
    tenant_aware: bool = False,
) -> RouteRule:
    """Compile a path template into a rule.

    ``{name}`` matches a single path segment; ``{name:path}`` matches across
    segments, the Starlette ``path`` converter (a resource uri carries slashes,
    #1141). ``**`` matches the rest of the path (used for the ``/auth``
    subtree, which is uniformly admin-only).
    """
    escaped = re.escape(template)
    escaped = escaped.replace(r"\{", "{").replace(r"\}", "}")
    regex = re.sub(
        r"\{[a-zA-Z_][a-zA-Z_0-9]*(:path)?\}",
        lambda m: ".+" if m.group(1) else "[^/]+",
        escaped,
    )
    regex = regex.replace(r"/\*\*", "(?:/.*)?")
    method_set = frozenset(m.strip().upper() for m in methods.split(",")) if methods else None
    return RouteRule(
        pattern=re.compile(regex),
        methods=method_set,
        permission=permission,
        template=f"{methods or 'ANY'} {template}",
        tenant_aware=tenant_aware,
    )


# Ordered: specific templates first. Paths are relative to the /api mount.
ROUTE_PERMISSIONS: tuple[RouteRule, ...] = (
    # --- MCP servers -------------------------------------------------------
    # L7 egress policy is the enforcement-plane channel: admin-only (ADR-013).
    _rule("/mcp_servers/{id}/l7_policy", "GET", ("policy", "read")),
    _rule("/mcp_servers/{id}/l7_policy", "POST,PUT,DELETE", ("policy", "write")),
    _rule("/mcp_servers/{id}/start", "POST", ("mcp_servers", "lifecycle")),
    _rule("/mcp_servers/{id}/stop", "POST", ("mcp_servers", "lifecycle")),
    _rule("/mcp_servers/{id}/block", "POST", ("mcp_servers", "lifecycle")),
    # Tenant-aware: a tenant grant reads only its own tenant's invocations.
    _rule("/mcp_servers/{id}/tools/history", "GET", ("mcp_servers", "read"), tenant_aware=True),
    _rule("/mcp_servers/{id}/tools", "GET", ("mcp_servers", "read")),
    _rule("/mcp_servers/{id}/health", "GET", ("mcp_servers", "read")),
    _rule("/mcp_servers/{id}/logs", "GET", ("mcp_servers", "read")),
    _rule("/mcp_servers/{id}", "GET", ("mcp_servers", "read")),
    _rule("/mcp_servers/{id}", "PUT,PATCH,DELETE", ("mcp_servers", "write")),
    _rule("/mcp_servers", "GET", ("mcp_servers", "read")),
    _rule("/mcp_servers", "POST", ("mcp_servers", "write")),
    # --- Sessions ----------------------------------------------------------
    # Suspending a session halts traffic to a running server: lifecycle.
    _rule("/sessions/{id}/suspend", "POST,DELETE", ("mcp_servers", "lifecycle")),
    # --- Groups ------------------------------------------------------------
    _rule("/groups/{id}/members/{member_id}", "DELETE", ("group", "update")),
    _rule("/groups/{id}/members", "POST", ("group", "update")),
    _rule("/groups/{id}/rebalance", "POST", ("group", "update")),
    _rule("/groups/{id}", "GET", ("group", "read")),
    _rule("/groups/{id}", "PUT", ("group", "update")),
    _rule("/groups/{id}", "DELETE", ("group", "delete")),
    _rule("/groups", "GET", ("group", "read")),
    _rule("/groups", "POST", ("group", "create")),
    # --- Discovery ---------------------------------------------------------
    # Source CRUD reuses discovery:approve (provider-admin + admin). The
    # vocabulary has no discovery:write; adding one would strand custom roles,
    # so the most privileged existing discovery permission gates it. Revisit if
    # a discovery:write constant is ever introduced.
    _rule("/discovery/sources/{source_id}/scan", "POST", ("discovery", "trigger")),
    _rule("/discovery/sources/{source_id}/enable", "PUT", ("discovery", "approve")),
    _rule("/discovery/sources/{source_id}", "PUT,DELETE", ("discovery", "approve")),
    _rule("/discovery/sources", "GET", ("discovery", "read")),
    _rule("/discovery/sources", "POST", ("discovery", "approve")),
    _rule("/discovery/pending", "GET", ("discovery", "read")),
    _rule("/discovery/quarantined", "GET", ("discovery", "read")),
    _rule("/discovery/approve/{name}", "POST", ("discovery", "approve")),
    _rule("/discovery/reject/{name}", "POST", ("discovery", "approve")),
    # --- Config ------------------------------------------------------------
    # Reload re-applies every governance input and launches servers: admin-only.
    _rule("/config/reload", "POST", ("config", "reload")),
    _rule("/config/backup", "POST", ("config", "update")),
    _rule("/config/export", "POST", ("config", "read")),
    _rule("/config/diff", "GET", ("config", "read")),
    _rule("/config", "GET", ("config", "read")),
    # --- Tools -------------------------------------------------------------
    _rule("/tools", "GET", ("tool", "list")),
    # `{tool:path}`: a resource is withdrawn by its upstream uri, slashes and all.
    # Tenant-aware: a tenant grant withdraws and restores for its own tenant
    # only; acting for every tenant needs a global grant.
    _rule("/admin/tools/{server}/{tool:path}/withdraw", "POST", ("mcp_servers", "lifecycle"), tenant_aware=True),
    _rule("/admin/tools/{server}/{tool:path}/restore", "POST", ("mcp_servers", "lifecycle"), tenant_aware=True),
    # --- WebSocket ---------------------------------------------------------
    # /ws/events streams EVERY domain event -- auth, quota, tenancy, tool
    # arguments -- so it is an audit-grade read, not a dashboard read.
    # Tenant-aware: a tenant grant receives only events that name its tenant.
    _rule("/ws/events", None, ("audit", "read"), tenant_aware=True),
    # --- System ------------------------------------------------------------
    # /system/me describes the calling principal; the principal is the resource.
    _rule("/system/me", "GET", AUTHENTICATED_ONLY),
    _rule("/system", "GET", AUTHENTICATED_ONLY),
    # --- Approvals ---------------------------------------------------------
    # approval:resolve is additionally enforced at the command handler
    # (ADR-016 D1); this rule makes the REST edge agree with it.
    # Tenant-aware: a tenant grant sees and resolves only approvals naming its
    # tenant; one naming no tenant is withheld (routes.py, commands/resolve.py).
    _rule("/approvals/{approval_id}/resolve", "POST", ("approval", "resolve"), tenant_aware=True),
    _rule("/approvals/{approval_id}", "GET", ("approval", "read"), tenant_aware=True),
    _rule("/approvals", "GET", ("approval", "read"), tenant_aware=True),
    # --- Auth administration ----------------------------------------------
    # API keys, roles, principals, permissions and tool-access policies.
    # ("admin", "*") is satisfiable only by PERMISSION_ADMIN_ALL.
    _rule("/auth/**", None, ("admin", "*")),
)


def resolve_rule(method: str, path: str) -> RouteRule | None:
    """Return the rule governing ``method path``, or None when unmatched.

    An unmatched route is denied by the caller. Returning None rather than a
    permissive default is the whole point of the table.
    """
    normalized = path.rstrip("/") or "/"
    for rule in ROUTE_PERMISSIONS:
        if rule.matches(method, normalized):
            return rule
    return None
