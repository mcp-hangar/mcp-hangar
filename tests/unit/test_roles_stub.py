"""`domain.security.roles` re-exports `auth.roles`; the two surfaces must not drift.

The module exists so the domain layer can name a role without importing `auth`
directly. It resolves the names through `importlib` at import time, so a name
renamed in `auth.roles` does not fail there -- it silently falls through to the
stub's `None`/empty default and the caller sees a role that does not exist.

The tests it replaced asserted `result is None or hasattr(result, "name")`, which
holds whichever branch ran and so could not fail.
"""

import importlib

from mcp_hangar.domain.security import roles as stub


def test_every_exported_name_is_the_object_auth_defines():
    auth_roles = importlib.import_module("mcp_hangar.auth.roles")

    for name in stub.__all__:
        assert getattr(stub, name) is getattr(auth_roles, name), f"{name} is not auth.roles.{name}"


def test_an_unknown_role_or_permission_resolves_to_nothing():
    assert stub.get_builtin_role("nonexistent_role") is None
    assert stub.get_permission("nonexistent:permission") is None


def test_the_builtin_roles_are_actually_populated():
    # The stub's fallback branch answers `{}` / `[]` for all of these. Asserting
    # they are non-empty is what tells the two branches apart.
    assert stub.BUILTIN_ROLES
    assert stub.PERMISSIONS
    assert stub.list_builtin_roles()
    assert stub.list_permissions()
