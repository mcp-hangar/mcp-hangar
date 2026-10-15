"""`GET /api/auth/permissions` lists the permissions the system grants (#1673).

It imported `ACTIONS` and `RESOURCE_TYPES`, which no longer exist, so it always
fell back to a hard-coded list that named none of `mcp_servers`, `policy`,
`approval` or `discovery`. The manifest is now built from the registry the
built-in roles are made of, and these tests hold it to what the API actually
asks for.
"""

from __future__ import annotations

import asyncio
import json

from mcp_hangar.auth.api.routes import list_permissions, permission_manifest
from mcp_hangar.auth.roles import BUILTIN_ROLES, PERMISSIONS
from mcp_hangar.server.api.route_permissions import ROUTE_PERMISSIONS


def _as_pairs(manifest):
    return {(row["resource_type"], action) for row in manifest for action in row["actions"]}


def test_the_manifest_is_exactly_the_registry():
    registry = {(p.resource_type, p.action) for p in PERMISSIONS.values()}
    assert _as_pairs(permission_manifest()) == registry


def test_the_resources_the_old_list_omitted_are_there():
    resources = {row["resource_type"] for row in permission_manifest()}
    assert {"mcp_servers", "policy", "approval", "discovery"} <= resources


def test_every_permission_a_route_asks_for_is_in_the_manifest():
    """A route that checks a pair the manifest does not list could never be granted by name."""
    listed = _as_pairs(permission_manifest())
    asked = {rule.permission for rule in ROUTE_PERMISSIONS if rule.permission is not None}
    # `("admin", "*")` is satisfied only by the wildcard grant, which the manifest lists as `*:*`.
    missing = {pair for pair in asked if pair not in listed and pair != ("admin", "*")}
    assert not missing, f"routes check these pairs and the manifest does not list them: {sorted(missing)}"


def test_every_permission_a_builtin_role_holds_is_in_the_manifest():
    listed = _as_pairs(permission_manifest())
    held = {(p.resource_type, p.action) for role in BUILTIN_ROLES.values() for p in role.permissions}
    assert held <= listed, sorted(held - listed)


def test_the_route_answers_the_manifest():
    response = asyncio.run(list_permissions(None))
    assert json.loads(response.body)["permissions"] == permission_manifest()
