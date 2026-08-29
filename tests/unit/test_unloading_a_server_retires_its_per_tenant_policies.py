"""Unloading an mcp_server retires its per-tenant policies too (#1138, #1142).

``remove_mcp_server_policy`` -- the hot-unload path -- cleared the server's own
policies for every kind (#1028) and left the ``tool_access.member.<tenant>``
ones behind. The cache that might have masked them is correctly cleared on
unload, so a server later loaded under the same id, declaring no
``tool_access:`` at all, was governed by its predecessor's per-tenant rules.

Every case verifies through public surfaces only: the registry through
``iter_registered_policies(kind=None)`` and the decision through
``is_governed_allowed``, the path production takes.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mcp_hangar.application.read_models.tool_projection import reset_tool_projection_registry
from mcp_hangar.domain.services.tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.fastmcp_server.flat_tool_projection import is_governed_allowed

_SERVER = "billing"
_TENANT = "tenant:a"


@pytest.fixture(autouse=True)
def _clean_state():
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    with patch("mcp_hangar.fastmcp_server.flat_tool_projection._member_to_group", return_value={}):
        yield
    reset_tool_access_resolver()
    reset_tool_projection_registry()


def _unload() -> None:
    get_tool_access_resolver().remove_mcp_server_policy(_SERVER)


def _registered() -> list[str]:
    return [key for key, _ in get_tool_access_resolver().iter_registered_policies(kind=None)]


def test_no_policy_for_the_id_remains_after_unload() -> None:
    resolver = get_tool_access_resolver()
    resolver.set_mcp_server_policy(_SERVER, ToolAccessPolicy(deny_list=["refund"]))
    resolver.set_standalone_member_policy(_SERVER, _TENANT, ToolAccessPolicy(deny_list=["refund"]))
    assert _registered() == [f"mcp_server:{_SERVER}", f"mcp_server:{_SERVER}:member:{_TENANT}"]

    _unload()

    assert _registered() == []


def test_a_server_reloaded_under_a_used_id_is_unrestricted_for_a_tenant_its_predecessor_denied() -> None:
    get_tool_access_resolver().set_standalone_member_policy(_SERVER, _TENANT, ToolAccessPolicy(deny_list=["refund"]))
    assert not is_governed_allowed(_SERVER, "refund", kind="tool", tenant_id=_TENANT)

    _unload()
    # The successor under the same id declares no ``tool_access:`` block.

    assert is_governed_allowed(_SERVER, "refund", kind="tool", tenant_id=_TENANT)


def test_a_stale_allow_list_does_not_survive_the_unload_either() -> None:
    """The fail-open half: an allow_list that outlives its server does not shout."""
    get_tool_access_resolver().set_standalone_member_policy(_SERVER, _TENANT, ToolAccessPolicy(allow_list=["refund"]))
    assert not is_governed_allowed(_SERVER, "invoice", kind="tool", tenant_id=_TENANT)

    _unload()

    assert _registered() == []
    assert is_governed_allowed(_SERVER, "invoice", kind="tool", tenant_id=_TENANT)


def test_a_per_tenant_prompt_policy_does_not_outlive_its_server() -> None:
    get_tool_access_resolver().set_standalone_member_policy(
        _SERVER, _TENANT, ToolAccessPolicy(deny_list=["p1"]), kind="prompt"
    )
    assert _registered() == [f"mcp_server:{_SERVER}:member:{_TENANT}[prompt]"]
    assert not is_governed_allowed(_SERVER, "p1", kind="prompt", tenant_id=_TENANT)

    _unload()

    assert _registered() == []
    assert is_governed_allowed(_SERVER, "p1", kind="prompt", tenant_id=_TENANT)


def test_another_servers_per_tenant_policies_are_left_alone() -> None:
    resolver = get_tool_access_resolver()
    resolver.set_standalone_member_policy(_SERVER, _TENANT, ToolAccessPolicy(deny_list=["refund"]))
    resolver.set_standalone_member_policy("ledger", _TENANT, ToolAccessPolicy(deny_list=["post"]))

    _unload()

    assert _registered() == [f"mcp_server:ledger:member:{_TENANT}"]
    assert not is_governed_allowed("ledger", "post", kind="tool", tenant_id=_TENANT)
