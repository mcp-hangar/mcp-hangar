"""Cross-tenant cache isolation for projected lists (issue #292, SEP-2549).

The per-tenant projected ``tools/list`` advertises a ``cacheScope`` under the
result ``_meta`` so a downstream cache can never serve one tenant's list to
another.  These tests pin the fail-closed, per-tenant properties of the pure
scope-derivation helper and the assembled ``_meta`` block.
"""

from __future__ import annotations

from mcp_hangar.fastmcp_server.flat_tool_projection import (
    CACHE_SCOPE_META_KEY,
    CACHE_TTL_META_KEY,
    PROJECTED_LIST_CACHE_TTL_MS,
    build_projected_list_cache_meta,
    derive_tenant_cache_scope,
)


def _scope(tenant_id: str | None) -> str:
    scope = build_projected_list_cache_meta(tenant_id)[CACHE_SCOPE_META_KEY]
    assert isinstance(scope, str)
    return scope


def test_distinct_tenants_get_distinct_cache_scope() -> None:
    """Tenant A and tenant B MUST advertise DIFFERENT cacheScope values."""
    scope_a = _scope("tenant-a")
    scope_b = _scope("tenant-b")

    assert scope_a != scope_b


def test_same_tenant_is_stable() -> None:
    """The same tenant queried twice MUST get the SAME (stable) cacheScope."""
    assert _scope("tenant-a") == _scope("tenant-a")
    assert derive_tenant_cache_scope("tenant-a") == derive_tenant_cache_scope("tenant-a")


def test_unknown_tenant_fails_closed_to_non_shareable_scope() -> None:
    """Unknown tenant -> narrowest, non-shareable, unique-per-request scope."""
    scope_none_1 = _scope(None)
    scope_none_2 = _scope(None)
    scope_empty = _scope("")

    # Non-shareable: a fresh token every time, so a cache can never reuse it.
    assert scope_none_1 != scope_none_2
    assert scope_none_1 != scope_empty

    # And it can never equal any real tenant's shareable scope.
    assert scope_none_1 != _scope("tenant-a")
    assert scope_none_1 != _scope("tenant-b")


def test_cache_scope_is_never_a_shared_global_constant() -> None:
    """No two DIFFERENT tenants may ever share one global scope constant."""
    tenants = ["t1", "t2", "t3", "acme", "globex", "initech"]
    scopes = {derive_tenant_cache_scope(t) for t in tenants}

    # Every distinct tenant produced a distinct scope -> no shared constant.
    assert len(scopes) == len(tenants)


def test_raw_tenant_id_does_not_leak_into_scope() -> None:
    """The advertised scope must not embed the raw tenant identifier."""
    secret = "super-secret-tenant-id"
    scope = derive_tenant_cache_scope(secret)

    assert secret not in scope


def test_meta_block_carries_conservative_ttl() -> None:
    """The _meta block advertises the conservative SEP-2549 ttlMs constant."""
    meta = build_projected_list_cache_meta("tenant-a")

    assert meta[CACHE_TTL_META_KEY] == PROJECTED_LIST_CACHE_TTL_MS
    assert isinstance(meta[CACHE_TTL_META_KEY], int)
    assert meta[CACHE_TTL_META_KEY] > 0
    assert CACHE_SCOPE_META_KEY in meta
