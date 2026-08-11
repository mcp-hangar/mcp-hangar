"""Every permission the registry defines must be enforced somewhere.

A permission string that no code path ever checks is worse than no permission:
it appears in ``GET /auth/permissions``, it gets granted to roles, it shows up
in access reviews -- and it restricts nothing. Before this gate existed, 27 of
31 registry entries were in exactly that state, including ``policy:write``
(admin-only on paper, while the egress-policy endpoint it was written for was
gated on a permission ``ROLE_DEVELOPER`` holds) and ``approval:read`` (granted
to the auditor role and checked nowhere).

"Enforced" means the permission's ``(resource_type, action)`` pair is either
required by a rule in :mod:`mcp_hangar.server.api.route_permissions`, or passed
to an ``authorize()``/``_check_permission()`` call site in the source tree.

This is a ratchet, and it bites in both directions:

* a permission that is neither enforced nor exempt fails the test, so a new
  unenforced permission cannot be added;
* an exemption for a permission that HAS since become enforced also fails, so
  the exemption list cannot rot into a list of stale excuses.

Shrinking ``UNENFORCED_BY_DESIGN`` is the unit of progress here.
"""

import ast
import pathlib

from mcp_hangar.auth.roles import PERMISSIONS
from mcp_hangar.server.api.route_permissions import ROUTE_PERMISSIONS
from mcp_hangar.server.tools.tool_permissions import TOOL_PERMISSIONS

SRC_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src"

# Call names that constitute an enforcement site.
_ENFORCEMENT_CALLS = frozenset({"authorize", "_check_permission", "check"})

# Permissions deliberately not enforced today, each with the reason and the
# disposition. Remove an entry when the permission becomes enforced -- or when
# it is deleted from the registry, which is the right outcome for most of these.
UNENFORCED_BY_DESIGN: dict[str, str] = {
    # --- Legacy vocabulary from the Provider -> McpServer rename -------------
    # These map to resource_type "provider". The REST API and the tool-invoke
    # path both authorize against the "mcp_servers" vocabulary instead, so the
    # provider:* family is residue: it should be DELETED from the registry
    # (with a deprecation note in UPGRADE.md), not wired up. Kept listed here
    # so the debt is visible and countable rather than silently tolerated.
    "mcp_server:create": "legacy provider:* vocabulary; superseded by mcp_servers:write",
    "mcp_server:read": "legacy provider:* vocabulary; superseded by mcp_servers:read",
    "mcp_server:update": "legacy provider:* vocabulary; superseded by mcp_servers:write",
    "mcp_server:delete": "legacy provider:* vocabulary; superseded by mcp_servers:write",
    "mcp_server:list": "legacy provider:* vocabulary; superseded by mcp_servers:read",
    "mcp_server:start": "legacy provider:* vocabulary; superseded by mcp_servers:lifecycle",
    "mcp_server:stop": "legacy provider:* vocabulary; superseded by mcp_servers:lifecycle",
    "provider:load": "legacy provider:* vocabulary; hangar_load authorizes as mcp_servers:write (#909)",
    "provider:load:verified": "unenforceable as written -- force_unverified is a caller-supplied argument",
    "provider:load:any": "unenforceable as written -- force_unverified is a caller-supplied argument",
    "provider:unload": "legacy provider:* vocabulary; hangar_unload authorizes as mcp_servers:write (#909)",
    # --- Redundant -----------------------------------------------------------
    "group:list": "listing is authorized as group:read; delete this entry or split the rule",
}


def _pairs_required_by_routes() -> set[tuple[str, str]]:
    return {rule.permission for rule in ROUTE_PERMISSIONS if rule.permission is not None}


def _pairs_required_by_tools() -> set[tuple[str, str]]:
    """Permissions the MCP tool surface requires (#909).

    A second declarative table beside the route one. Both are read directly
    rather than scanned, because a table is the shape this ledger can see
    exactly; the AST scan below only catches literal `authorize()` arguments.
    """
    return set(TOOL_PERMISSIONS.values())


def _pairs_required_by_code() -> set[tuple[str, str]]:
    """Collect (resource_type, action) pairs from enforcement call sites.

    Only literal keyword arguments are recognised. A call that computes its
    permission dynamically is invisible here -- which is itself a reason to
    keep permissions literal at the call site.
    """
    pairs: set[tuple[str, str]] = set()
    for path in SRC_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - source tree is parseable
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if name not in _ENFORCEMENT_CALLS:
                continue
            kwargs = {kw.arg: kw.value for kw in node.keywords}
            resource_type, action = kwargs.get("resource_type"), kwargs.get("action")
            if isinstance(resource_type, ast.Constant) and isinstance(action, ast.Constant):
                pairs.add((resource_type.value, action.value))
    return pairs


def _enforced_permission_keys() -> set[str]:
    enforced_pairs = _pairs_required_by_routes() | _pairs_required_by_tools() | _pairs_required_by_code()
    keys: set[str] = set()
    for key, permission in PERMISSIONS.items():
        pair = (permission.resource_type, permission.action)
        # The admin wildcard is satisfied by, and satisfies, every gate.
        if pair == ("*", "*") or pair in enforced_pairs:
            keys.add(key)
    return keys


class TestDefinedImpliesEnforced:
    def test_no_permission_is_defined_without_an_enforcement_site(self):
        unenforced = sorted(set(PERMISSIONS) - _enforced_permission_keys())
        undocumented = [key for key in unenforced if key not in UNENFORCED_BY_DESIGN]
        assert undocumented == [], (
            "These permissions are granted to roles but checked nowhere. Either "
            "add an enforcement site (a rule in route_permissions.py or an "
            "authorize() call), or delete them from the registry. Adding them to "
            "UNENFORCED_BY_DESIGN requires a reason: " + ", ".join(undocumented)
        )

    def test_exemptions_are_not_stale(self):
        """An exemption for a now-enforced permission must be removed."""
        enforced = _enforced_permission_keys()
        stale = sorted(key for key in UNENFORCED_BY_DESIGN if key in enforced)
        assert stale == [], "These permissions are now enforced -- remove them from UNENFORCED_BY_DESIGN: " + ", ".join(
            stale
        )

    def test_exemptions_reference_real_permissions(self):
        unknown = sorted(key for key in UNENFORCED_BY_DESIGN if key not in PERMISSIONS)
        assert unknown == [], f"UNENFORCED_BY_DESIGN names permissions that no longer exist: {unknown}"

    def test_enforcement_debt_does_not_grow(self):
        """A hard ceiling, so the exemption list can only shrink.

        Lower this number when you clear entries. Raising it is a review-visible
        act, which is the point.
        """
        assert len(UNENFORCED_BY_DESIGN) <= 12


class TestCriticalPermissionsAreEnforced:
    """The specific permissions whose absence was exploitable."""

    def test_policy_write_is_enforced(self):
        assert "policy:write" in _enforced_permission_keys()

    def test_config_reload_is_enforced(self):
        assert "config:reload" in _enforced_permission_keys()

    def test_approval_read_is_enforced(self):
        assert "approval:read" in _enforced_permission_keys()

    def test_approval_resolve_is_enforced(self):
        assert "approval:resolve" in _enforced_permission_keys()

    def test_discovery_approve_is_enforced(self):
        assert "discovery:approve" in _enforced_permission_keys()
