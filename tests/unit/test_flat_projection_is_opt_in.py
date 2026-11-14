"""A front door projects only the tools ``tool_projection.flat`` names (#1372).

Each server and group on a front door names the tools it projects under their
own names, and the configuration is refused at load when one names none, names
a tool its own ``tools:`` hides, uses a wildcard, or sets the key under egress.
The key only narrows the projection: what policy, a withdrawal or a collision
removes, naming it does not bring back. Prompts and resources do not read it.

The served path, and a call of a tool the key does not name, are in
``tests/integration/test_a_front_door_projects_only_the_tools_flat_names.py``
and, over real streamable HTTP, ``tests/live/test_t0_front_door_flat_is_opt_in.py``.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Any

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    add_change_listener,
    get_tool_projection_registry,
    remove_change_listener,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.domain.value_objects.tool_digest import ToolDigest
from mcp_hangar.fastmcp_server import prompt_proxy
from mcp_hangar.fastmcp_server.flat_tool_projection import _build_flat_map, generate_projection
from mcp_hangar.server.config import check_process_config, load_config
from mcp_hangar.server.config_schema import TOOL_PROJECTION_SCOPE_KEYS, TOOL_PROJECTION_TENANT_KEYS

SERVER = "store"
TENANT = "tenant:a"
OTHER = "tenant:b"


def _spec(**extra: Any) -> dict[str, Any]:
    return {"mode": "subprocess", "command": [sys.executable, "-c", "pass"], **extra}


def _front_door(**servers: dict[str, Any]) -> dict[str, Any]:
    return {"tool_access": {"mode": "front_door"}, "mcp_servers": servers}


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    get_tool_access_resolver().set_topology_mode("front_door")
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()


def _catalogue(*names: str, server: str = SERVER) -> None:
    get_tool_projection_registry().build_from_tools(
        server, [ToolSchema(name=name, description=name, input_schema={"type": "object"}) for name in names]
    )


class TestAFrontDoorChoosesItsSurface:
    def test_a_server_with_no_flat_key_refuses_to_start_and_names_the_decision(self) -> None:
        with pytest.raises(ConfigurationError) as refused:
            check_process_config(_front_door(**{SERVER: _spec()}))

        message = str(refused.value)
        assert "tool_projection.flat" in message
        assert f"mcp_servers.{SERVER}" in message
        assert "must be chosen explicitly" in message
        assert "invalid" not in message.lower(), "the surface is a decision left open, not a malformed file"

    def test_a_group_with_no_flat_key_is_refused_the_same_way(self) -> None:
        config = _front_door(**{SERVER: _spec(tool_projection={"flat": []}), "pool": {"mode": "group"}})

        with pytest.raises(ConfigurationError, match=r"mcp_servers\.pool .*must be chosen explicitly"):
            check_process_config(config)

    def test_an_empty_list_is_a_choice(self) -> None:
        check_process_config(_front_door(**{SERVER: _spec(tool_projection={"flat": []})}))

    def test_a_front_door_with_no_servers_has_nothing_to_choose(self) -> None:
        check_process_config(_front_door())


class TestEgressRefusesTheKey:
    def test_flat_under_egress_is_contradictory(self) -> None:
        config = {"mcp_servers": {SERVER: _spec(tool_projection={"flat": ["add"]})}}

        with pytest.raises(ConfigurationError, match=r"tool_projection\.flat is set on an egress gateway.*ADR-027"):
            check_process_config(config)

    def test_egress_without_the_key_is_unchanged(self) -> None:
        check_process_config({"mcp_servers": {SERVER: _spec()}})


class TestTheSchemaTakesNoWildcard:
    @pytest.mark.parametrize(
        "flat",
        ["all", "*", None, {"add": True}, ["all"], ["*"], ["read_*"], ["get?"], ["[ab]dd"], [""], [1], [None]],
        ids=repr,
    )
    def test_anything_but_a_list_of_names_is_refused(self, flat: Any) -> None:
        with pytest.raises(ConfigurationError, match=r"tool_projection\.flat for 'store'"):
            load_config({SERVER: _spec(tool_projection={"flat": flat})})

    def test_the_refusal_is_the_loaders_and_the_process_checks(self) -> None:
        with pytest.raises(ConfigurationError, match="no wildcard"):
            check_process_config(_front_door(**{SERVER: _spec(tool_projection={"flat": ["*"]})}))

    def test_flat_is_a_scope_key_and_not_a_tenant_key(self) -> None:
        assert "flat" in TOOL_PROJECTION_SCOPE_KEYS
        assert "flat" not in TOOL_PROJECTION_TENANT_KEYS


class TestFlatNamesOnlyWhatToolsMakesVisible:
    def test_a_name_the_tools_list_does_not_declare_is_refused_naming_both_halves(self) -> None:
        tools = [{"name": "add", "inputSchema": {}}]
        config = _front_door(**{SERVER: _spec(tools=tools, tool_projection={"flat": ["add", "subtract"]})})

        with pytest.raises(ConfigurationError) as refused:
            check_process_config(config)

        message = str(refused.value)
        assert f"mcp_servers.{SERVER}.tool_projection.flat names ['subtract']" in message
        assert f"mcp_servers.{SERVER}.tools does not make visible" in message

    @pytest.mark.parametrize(
        "policy", [{"deny_list": ["subtract"]}, {"allow_list": ["add"]}, {"allow_list": ["a*"]}], ids=repr
    )
    def test_a_name_the_tools_policy_hides_is_refused(self, policy: dict[str, list[str]]) -> None:
        config = _front_door(**{SERVER: _spec(tools=policy, tool_projection={"flat": ["add", "subtract"]})})

        with pytest.raises(ConfigurationError, match=r"names \['subtract'\], which mcp_servers\.store\.tools"):
            check_process_config(config)

    def test_a_group_policy_is_read_for_the_group(self) -> None:
        group = {"mode": "group", "tools": {"deny_list": ["add"]}, "tool_projection": {"flat": ["add"]}}

        with pytest.raises(ConfigurationError, match=r"mcp_servers\.pool\.tool_projection\.flat names \['add'\]"):
            check_process_config(_front_door(pool=group))

    def test_an_approval_listed_tool_is_visible(self) -> None:
        policy = {"approval_list": ["add"]}
        check_process_config(_front_door(**{SERVER: _spec(tools=policy, tool_projection={"flat": ["add"]})}))

    def test_with_no_tools_key_nothing_is_known_at_load(self) -> None:
        check_process_config(_front_door(**{SERVER: _spec(tool_projection={"flat": ["anything"]})}))

    def test_a_tools_mapping_that_declares_no_policy_hides_nothing(self) -> None:
        check_process_config(_front_door(**{SERVER: _spec(tools={}, tool_projection={"flat": ["add"]})}))

    def test_a_tools_policy_that_does_not_parse_is_left_to_the_loaders_refusal(self) -> None:
        config = _front_door(**{SERVER: _spec(tools={"deny_list": "add"}, tool_projection={"flat": ["add"]})})
        check_process_config(config)

        with pytest.raises(ConfigurationError, match="tools access policy"):
            load_config(config["mcp_servers"])

    @pytest.mark.parametrize("servers", [None, [], {SERVER: "not a mapping"}], ids=repr)
    def test_a_section_of_another_shape_is_left_to_its_own_check(self, servers: Any) -> None:
        from mcp_hangar.server.config import _check_flat_projection

        _check_flat_projection({"tool_access": {"mode": "front_door"}, "mcp_servers": servers})


class TestNoKeyIsInferredFromAnother:
    def test_a_pinned_tool_is_not_projected_unless_named(self) -> None:
        load_config({SERVER: _spec(tool_projection={"flat": ["add"], "pins": {"subtract": "a" * 64}})})
        _catalogue("add", "subtract")

        assert set(_build_flat_map(TENANT)) == {"add"}

    def test_a_declared_tool_is_not_projected_unless_named(self) -> None:
        tools = [{"name": "add"}, {"name": "subtract"}]
        load_config({SERVER: _spec(tools=tools, tool_projection={"flat": ["add"]})})
        _catalogue("add", "subtract")

        assert set(_build_flat_map(TENANT)) == {"add"}

    def test_naming_a_tool_pins_nothing_and_sets_no_policy(self) -> None:
        load_config({SERVER: _spec(tool_projection={"flat": ["add"]})})

        assert get_tool_projection_registry().resolve_pin(SERVER, "add", None) is None
        assert get_tool_access_resolver().get_configured_policy("provider", SERVER) is None


class TestItOnlyNarrows:
    def test_a_server_that_declared_none_projects_nothing(self) -> None:
        # Only a server registered over REST or by discovery can be here: a file is refused.
        _catalogue("add")

        assert _build_flat_map(TENANT) == {}

    def test_a_named_tool_the_upstream_does_not_serve_is_not_listed(self) -> None:
        get_tool_projection_registry().set_flat(SERVER, frozenset({"add", "missing"}))
        _catalogue("add")

        assert set(generate_projection(TENANT).routes) == {"add"}

    def test_a_named_tool_policy_denies_stays_hidden(self) -> None:
        get_tool_projection_registry().set_flat(SERVER, frozenset({"add", "subtract"}))
        _catalogue("add", "subtract")
        get_tool_access_resolver().set_standalone_member_policy(SERVER, OTHER, ToolAccessPolicy(deny_list=("add",)))

        assert set(generate_projection(TENANT).routes) == {"add", "subtract"}
        assert set(generate_projection(OTHER).routes) == {"subtract"}

    def test_a_named_tool_that_is_withdrawn_stays_hidden(self) -> None:
        registry = get_tool_projection_registry()
        registry.set_flat(SERVER, frozenset({"add"}))
        _catalogue("add")
        registry.withdraw(SERVER, "add", TENANT)

        assert generate_projection(TENANT).routes == {}

    def test_a_named_tool_whose_pin_drifted_stays_hidden(self) -> None:
        registry = get_tool_projection_registry()
        registry.set_flat(SERVER, frozenset({"add"}))
        _catalogue("add")
        registry.set_config_pin(SERVER, "add", None, ToolDigest(tool_name="add", sha256="0" * 64))

        assert generate_projection(TENANT).routes == {}

    def test_a_collision_stays_dropped_when_only_one_side_names_the_tool(self) -> None:
        # The other server's `add` is not projected, and a collision it caused is not undone by it.
        registry = get_tool_projection_registry()
        registry.set_flat(SERVER, frozenset({"add"}))
        registry.set_flat("other", frozenset())
        _catalogue("add")
        _catalogue("add", server="other")

        assert generate_projection(TENANT).routes == {}

    def test_no_identity_is_still_served_nothing(self) -> None:
        get_tool_projection_registry().set_flat(SERVER, frozenset({"add"}))
        _catalogue("add")

        assert generate_projection(None).routes == {}

    def test_a_member_of_one_group_is_projected_by_its_groups_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp_hangar.fastmcp_server import flat_tool_projection

        member = type("Member", (), {"id": "m1"})()
        group = type("Group", (), {"id": "pool", "members": [member], "route_candidates": lambda self, t: (member,)})()
        monkeypatch.setattr(flat_tool_projection, "_groups", lambda: {"pool": group})
        monkeypatch.setattr(flat_tool_projection, "_member_to_groups", lambda: {"m1": ("pool",)})
        registry = get_tool_projection_registry()
        registry.set_flat("m1", frozenset({"add", "subtract"}))
        registry.set_flat("pool", frozenset({"add"}))
        _catalogue("add", "subtract", server="m1")

        assert dict(generate_projection(TENANT).routes) == {"add": ("m1", "add")}


class TestOtherSurfacesDoNotReadIt:
    def test_an_upstream_with_no_flat_tool_still_serves_its_prompts_and_resources(self) -> None:
        get_tool_projection_registry().set_flat(SERVER, frozenset())
        _catalogue("add")

        assert _build_flat_map(TENANT) == {}
        assert prompt_proxy._upstream_ids(TENANT) == [SERVER]


class TestAChangeIsAnnounced:
    def test_a_new_list_tells_the_listeners(self) -> None:
        told: list[None] = []

        def listener() -> None:
            told.append(None)

        add_change_listener(listener)
        try:
            get_tool_projection_registry().set_flat(SERVER, frozenset({"add"}))
        finally:
            remove_change_listener(listener)

        assert told

    def test_a_reload_replaces_the_lists_with_the_new_files(self) -> None:
        load_config({SERVER: _spec(tool_projection={"flat": ["add"]})})
        _catalogue("add", "subtract")
        assert set(_build_flat_map(TENANT)) == {"add"}

        load_config({SERVER: _spec(tool_projection={"flat": ["subtract"]})}, replace=True)

        assert set(_build_flat_map(TENANT)) == {"subtract"}
