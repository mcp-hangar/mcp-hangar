"""Every server-scope policy change reaches every cached scope that read it (#2203).

The resolver caches each merge by scope key, and the group-branch key does not
name the server id it was asked with. A writer that dropped only the keys it
could name left the others resolving against the previous policy. The rule
lives in one predicate, ``_scope_reads_server``; this pins it as a property:
after any writer or remover, what every cached scope resolves to is what a
fresh merge computes.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from mcp_hangar.domain.services import _policy_merge
from mcp_hangar.domain.services.tool_access_resolver import ToolAccessResolver
from mcp_hangar.domain.value_objects import ToolAccessPolicy

TENANT = "tenant:a"
DENY = ToolAccessPolicy(deny_list=("add",))

#: (mcp_server_id, group_id, member_id, member_server_id), one of every key shape
#: `resolve_effective_policy` builds, asked the way `Target.policy_scopes` asks.
SCOPES: list[tuple[str, str | None, str | None, str | None]] = [
    ("plain", None, None, None),  # mcp_server:plain
    ("plain", None, TENANT, None),  # mcp_server:plain:member:tenant:a
    ("pool", "pool", TENANT, "mapped"),  # group:pool:member:mapped:tenant:tenant:a
    ("pool", "pool", None, "mapped"),  # group:pool:member:mapped
    ("pool", "pool", TENANT, "unmapped"),  # group:pool:member:unmapped:tenant:tenant:a
    ("mapped", None, TENANT, None),
    ("unmapped", None, TENANT, None),
]

#: The server ids worth changing: the floor, a plain server, a group id used as
#: a server id, a mapped member server and an unmapped one.
SERVER_IDS = ["_global", "plain", "pool", "mapped", "unmapped"]


def _resolver() -> ToolAccessResolver:
    resolver = ToolAccessResolver()
    resolver.set_member_policy("pool", "mapped", ToolAccessPolicy(deny_list=("other",)), mcp_server_id="mapped")
    return resolver


def _warm(resolver: ToolAccessResolver) -> None:
    for server_id, group_id, member_id, member_server_id in SCOPES:
        _policy_merge.resolve_effective_policy(
            resolver, server_id, group_id, member_id, member_server_id=member_server_id
        )


def _assert_fresh(resolver: ToolAccessResolver) -> None:
    for server_id, group_id, member_id, member_server_id in SCOPES:
        cached = _policy_merge.resolve_effective_policy(
            resolver, server_id, group_id, member_id, member_server_id=member_server_id
        )
        with resolver.locked():
            fresh = _policy_merge._compute_effective_policy(
                resolver, server_id, group_id, member_id, member_server_id=member_server_id
            )
        assert cached == fresh, (server_id, group_id, member_id, member_server_id)


WRITERS: dict[str, Callable[[ToolAccessResolver, str], None]] = {
    "set_mcp_server_policy": lambda r, sid: r.set_mcp_server_policy(sid, DENY),
    "set_standalone_member_policy": lambda r, sid: r.set_standalone_member_policy(sid, TENANT, DENY),
}


@pytest.mark.parametrize("server_id", SERVER_IDS)
@pytest.mark.parametrize("writer", sorted(WRITERS))
def test_a_write_reaches_every_cached_scope(writer: str, server_id: str) -> None:
    resolver = _resolver()
    _warm(resolver)

    WRITERS[writer](resolver, server_id)

    _assert_fresh(resolver)


@pytest.mark.parametrize("server_id", SERVER_IDS)
@pytest.mark.parametrize("writer", sorted(WRITERS))
def test_a_removal_reaches_every_cached_scope(writer: str, server_id: str) -> None:
    resolver = _resolver()
    WRITERS[writer](resolver, server_id)
    _warm(resolver)

    resolver.remove_mcp_server_policy(server_id)

    _assert_fresh(resolver)


@pytest.mark.parametrize("server_id", SERVER_IDS)
def test_invalidating_one_server_reaches_every_cached_scope(server_id: str) -> None:
    resolver = _resolver()
    _warm(resolver)
    # Changed behind the cache's back, which is what `invalidate_cache(id)` is for.
    resolver._mcp_server_policies[(server_id, "tool")] = DENY

    resolver.invalidate_cache(server_id)

    _assert_fresh(resolver)


def test_a_member_mapping_removed_without_a_policy_reaches_the_cache() -> None:
    resolver = ToolAccessResolver()
    resolver.set_mcp_server_policy("backend", DENY)
    resolver.set_member_policy("pool", "member", ToolAccessPolicy(), mcp_server_id="backend")
    before = _policy_merge.resolve_effective_policy(resolver, "pool", "pool", TENANT, member_server_id="member")
    assert before.deny_list == ("add",)

    resolver.remove_member_policy("pool", "member")

    after = _policy_merge.resolve_effective_policy(resolver, "pool", "pool", TENANT, member_server_id="member")
    assert after.is_unrestricted()


def test_an_unrelated_entry_survives() -> None:
    # The rule is not "clear everything": a change to one server keeps what
    # never read it.
    resolver = _resolver()
    _warm(resolver)

    resolver.set_mcp_server_policy("plain", DENY)

    assert ("tool", "mcp_server:unmapped:member:tenant:a") in resolver._policy_cache
    assert ("tool", "group:pool:member:mapped:tenant:tenant:a") in resolver._policy_cache


@pytest.mark.parametrize(
    ("scope_key", "server_id", "reads"),
    [
        ("mcp_server:s", "s", True),
        ("mcp_server:s:member:tenant:a", "s", True),
        ("mcp_server:s2", "s", False),
        ("mcp_server:s2:member:s", "s", False),
        ("group:g:member:m", "g", True),
        ("group:g:member:m:tenant:t", "m", True),
        ("group:g:member:m:tenant:s", "s", False),
        ("group:g:member:mapped-member", "backend", True),
        ("group:g2:member:mapped-member", "backend", False),
        ("group:g:member:tenant:a:tenant:tenant:a", "a", False),
        ("anything:else", "s", True),
        ("group:g", "s", True),
        ("mcp_server:s2", "_global", True),
    ],
)
def test_the_rule(scope_key: str, server_id: str, reads: bool) -> None:
    from mcp_hangar.domain.services.tool_access_resolver import _scope_reads_server

    mapping = {("g", "mapped-member"): "backend"}
    assert _scope_reads_server(scope_key, server_id, mapping) is reads
