"""A role grant that no authorizer will ever collect must be refused.

`RBACAuthorizer._collect_roles` asks the store for exactly two things: `global`,
and `tenant:{id}` when the principal carries a tenant. A grant written with any
other scope was accepted, persisted, and then never matched.

That fails closed, so it is not an escalation -- which is precisely why it is
easy to leave alone, and why it is worth closing. An administrator who grants
`scope="*"` believes a permission exists. It appears in the audit trail. It does
nothing. The usual next step, when the grant "does not work", is to reach for
something blunter and less auditable.

Found by an independent model review during a security audit (LLM-02) and
confirmed against `_collect_roles` before fixing.

The validation lives in the domain and is called by every store rather than by
the API handler, because a store reached directly -- by the CLI, by a migration,
by an embedder -- would otherwise still accept the silent grant.
"""

from __future__ import annotations

import inspect

import pytest

from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer
from mcp_hangar.domain.contracts.authorization import validate_role_scope


class TestTheValidatorMatchesWhatIsCollected:
    """The rule is not arbitrary: it is exactly what `_collect_roles` looks up."""

    @pytest.mark.parametrize("scope", ["global", "tenant:acme", "tenant:1", "tenant:a-b_c"])
    def test_a_collectable_scope_is_accepted(self, scope):
        validate_role_scope(scope)

    @pytest.mark.parametrize("scope", ["*", "all", "any", "tenant", "Global", "", "TENANT:acme"])
    def test_a_scope_nothing_collects_is_refused(self, scope):
        with pytest.raises(ValueError):
            validate_role_scope(scope)

    def test_the_message_says_what_to_use_instead(self):
        with pytest.raises(ValueError) as excinfo:
            validate_role_scope("*")
        message = str(excinfo.value)
        assert "global" in message and "tenant:" in message

    def test_the_rule_still_matches_the_collector(self):
        """If `_collect_roles` learns a third scope, this test says so.

        The validator and the collector have to agree, and nothing else makes
        them. Asserted as the exact SET of scope expressions the collector uses,
        not as "the string appears somewhere" -- the first draft did that, and a
        probe changing one of two occurrences slipped straight past it.
        """
        import ast
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(RBACAuthorizer._collect_roles)))
        scopes = {
            ast.unparse(kw.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "scope"
        }
        assert scopes == {"'global'", "tenant_scope"}, (
            f"_collect_roles now queries {scopes}; validate_role_scope must be "
            "updated to match, or grants in the new scope will be silently inert"
        )


class TestEveryStoreRefusesIt:
    """The API handler is not the only way in."""

    def test_the_in_memory_store_refuses(self):
        with pytest.raises(ValueError):
            InMemoryRoleStore().assign_role("svc:a", "viewer", scope="*")

    def test_it_still_accepts_the_real_scopes(self):
        store = InMemoryRoleStore()
        store.assign_role("svc:a", "viewer", scope="global")
        store.assign_role("svc:a", "viewer", scope="tenant:acme")
        assert store.get_roles_for_principal("svc:a", scope="global")

    @pytest.mark.parametrize(
        "module_path",
        [
            "mcp_hangar.auth.infrastructure.event_sourced_store",
            "mcp_hangar.auth.infrastructure.postgres_store",
            "mcp_hangar.auth.infrastructure.sqlite_store",
            "mcp_hangar.auth.infrastructure.rbac_authorizer",
        ],
    )
    def test_every_implementation_calls_the_validator(self, module_path):
        """Asserted per module: a fifth store added without the call is a silent hole."""
        import importlib
        import pathlib

        source = pathlib.Path(importlib.import_module(module_path).__file__).read_text(encoding="utf-8")
        assert "validate_role_scope(scope)" in source, (
            f"{module_path} implements assign_role without validating the scope"
        )


def test_a_refused_grant_is_not_persisted():
    """Refusing after writing would leave the audit trail claiming it happened."""
    store = InMemoryRoleStore()
    with pytest.raises(ValueError):
        store.assign_role("svc:a", "viewer", scope="*")
    assert store.get_roles_for_principal("svc:a", scope="*") == []
    assert store.get_roles_for_principal("svc:a", scope="global") == []
