"""A group member's own policy must reach the verdict for a call routed to it.

`groups.<g>.members.<m>.tools` and the REST `member/<g>:<m>` scope register a
policy under the member SERVER id. Every production caller identifies itself
with the caller's TENANT, so the group branch used to look the member policy up
under the tenant, miss, and answer with `_global -> group` alone -- a documented
deny_list that failed open (#1164).

These tests ask the question the production surfaces ask: tenant AND selected
member, not the resolver's internal vocabulary.
"""

from unittest.mock import patch

import pytest

from mcp_hangar.domain.services.tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.fastmcp_server.flat_tool_projection import is_governed_allowed

_GROUP = "secure-pool"
_MEMBER = "full-access"
_OTHER_MEMBER = "read-only"
_TENANT = "tenant:a"


@pytest.fixture(autouse=True)
def _clean_resolver():
    reset_tool_access_resolver()
    yield
    reset_tool_access_resolver()


def _configured_pool():
    """The `MCP_SERVER_GROUPS.md` example: group allows, one member denies."""
    resolver = get_tool_access_resolver()
    resolver.set_group_policy(_GROUP, ToolAccessPolicy(allow_list=("query_*", "admin_*")))
    resolver.set_member_policy(
        group_id=_GROUP,
        member_id=_MEMBER,
        policy=ToolAccessPolicy(deny_list=("admin_*",)),
        mcp_server_id=_MEMBER,
    )
    return resolver


def test_member_deny_list_refuses_a_call_routed_to_that_member():
    resolver = _configured_pool()

    assert not resolver.is_tool_allowed(
        _GROUP, "admin_reset", group_id=_GROUP, member_id=_TENANT, member_server_id=_MEMBER
    )
    assert resolver.is_tool_allowed(_GROUP, "query_rows", group_id=_GROUP, member_id=_TENANT, member_server_id=_MEMBER)


def test_a_sibling_member_is_not_governed_by_it():
    """The deny is the member's own, not the group's."""
    resolver = _configured_pool()

    assert resolver.is_tool_allowed(
        _GROUP, "admin_reset", group_id=_GROUP, member_id=_TENANT, member_server_id=_OTHER_MEMBER
    )


def test_the_member_server_policy_is_merged_too():
    """`mcp_servers.<m>.tools` on a server reached through a group."""
    resolver = _configured_pool()
    resolver.set_mcp_server_policy(_MEMBER, ToolAccessPolicy(deny_list=("query_secrets",)))

    assert not resolver.is_tool_allowed(
        _GROUP, "query_secrets", group_id=_GROUP, member_id=_TENANT, member_server_id=_MEMBER
    )


def test_the_callers_tenant_policy_on_the_member_is_merged_too():
    """`mcp_servers.<m>.tool_access.member.<tenant>` reached through a group."""
    resolver = _configured_pool()
    resolver.set_standalone_member_policy(_MEMBER, _TENANT, ToolAccessPolicy(deny_list=("query_pii",)))

    assert not resolver.is_tool_allowed(
        _GROUP, "query_pii", group_id=_GROUP, member_id=_TENANT, member_server_id=_MEMBER
    )
    assert resolver.is_tool_allowed(
        _GROUP, "query_pii", group_id=_GROUP, member_id="tenant:b", member_server_id=_MEMBER
    )


def test_two_members_under_one_tenant_do_not_share_a_cache_entry():
    """The verdict is per (member, tenant), so the cache key must be too."""
    resolver = _configured_pool()

    allowed_first = resolver.is_tool_allowed(
        _GROUP, "admin_reset", group_id=_GROUP, member_id=_TENANT, member_server_id=_OTHER_MEMBER
    )
    denied_second = resolver.is_tool_allowed(
        _GROUP, "admin_reset", group_id=_GROUP, member_id=_TENANT, member_server_id=_MEMBER
    )

    assert allowed_first and not denied_second


def test_a_policy_set_after_a_resolve_invalidates_the_cached_verdict():
    resolver = _configured_pool()
    assert resolver.is_tool_allowed(_GROUP, "query_rows", group_id=_GROUP, member_id=_TENANT, member_server_id=_MEMBER)

    resolver.set_member_policy(
        group_id=_GROUP,
        member_id=_MEMBER,
        policy=ToolAccessPolicy(deny_list=("admin_*", "query_rows")),
        mcp_server_id=_MEMBER,
    )

    assert not resolver.is_tool_allowed(
        _GROUP, "query_rows", group_id=_GROUP, member_id=_TENANT, member_server_id=_MEMBER
    )


def test_the_front_door_applies_the_member_policy_to_a_member_keyed_tool():
    """`is_governed_allowed` is asked with the member id; the group owns it."""
    _configured_pool()

    with (
        patch(
            "mcp_hangar.fastmcp_server.flat_tool_projection._member_to_group",
            return_value={_MEMBER: _GROUP, _OTHER_MEMBER: _GROUP},
        ),
        patch("mcp_hangar.fastmcp_server.flat_tool_projection._groups", return_value={_GROUP: object()}),
    ):
        assert not is_governed_allowed(_MEMBER, "admin_reset", kind="tool", tenant_id=_TENANT)
        assert is_governed_allowed(_MEMBER, "query_rows", kind="tool", tenant_id=_TENANT)
        assert is_governed_allowed(_OTHER_MEMBER, "admin_reset", kind="tool", tenant_id=_TENANT)


def test_the_resolvers_own_vocabulary_still_resolves():
    """A caller passing the member id as `member_id` (the pre-#1164 tests)."""
    resolver = _configured_pool()

    assert not resolver.is_tool_allowed(_MEMBER, "admin_reset", group_id=_GROUP, member_id=_MEMBER)
