"""A configuration's tool-access policies are adopted in one step (#1424).

A reload used to call `clear_all()` and then register the file's policies server
by server. Between the two, a call was resolved against no policies at all;
`clear_all()` also reset the topology mode, and it wiped every policy a runtime
caller had set. The file's policies are now registered on a fresh resolver and
adopted from it under one lock, with a record of which entries the file set:

* a policy the file no longer has is lifted, and an edited one takes effect;
* a policy set at runtime -- the REST endpoint, the agent's `_global` policy,
  `hangar_load` -- is kept, unless the file now defines the same scope, which
  then takes the file's policy;
* a first load adds to what is there.

The reload-level test, with a concurrent call made while the new configuration
is being built, is `test_a_reload_applies_the_whole_configuration.py`.
"""

from __future__ import annotations

from mcp_hangar.domain.services.tool_access_resolver import ToolAccessResolver
from mcp_hangar.domain.value_objects import ToolAccessPolicy

DENY_X = ToolAccessPolicy(deny_list=("x",))
DENY_Y = ToolAccessPolicy(deny_list=("y",))


def _file(
    resolver: ToolAccessResolver,
    *,
    replace: bool,
    servers: dict[str, ToolAccessPolicy] | None = None,
    groups: dict[str, ToolAccessPolicy] | None = None,
    members: dict[tuple[str, str], ToolAccessPolicy] | None = None,
    tenants: dict[tuple[str, str], ToolAccessPolicy] | None = None,
) -> None:
    """Register a configuration's policies aside, then adopt them: what `load_config` does."""
    staged = ToolAccessResolver()
    for server, policy in (servers or {}).items():
        staged.set_mcp_server_policy(server, policy)
    for group, policy in (groups or {}).items():
        staged.set_group_policy(group, policy)
    for (group, member), policy in (members or {}).items():
        staged.set_member_policy(group, member, policy, mcp_server_id=member)
    for (server, tenant), policy in (tenants or {}).items():
        staged.set_standalone_member_policy(server, tenant, policy)
    resolver.adopt_config_policies(staged, replace=replace)


def _denies(resolver: ToolAccessResolver, server: str, tool: str) -> bool:
    return not resolver.is_tool_allowed(server, tool)


class TestAReloadReplacesTheFilesPolicies:
    def test_a_policy_deleted_from_the_file_is_lifted(self) -> None:
        resolver = ToolAccessResolver()
        _file(resolver, replace=False, servers={"a": DENY_X, "b": DENY_X})

        _file(resolver, replace=True, servers={"a": DENY_X})

        assert _denies(resolver, "a", "x")
        assert not _denies(resolver, "b", "x")

    def test_an_edited_policy_takes_effect_past_the_cache(self) -> None:
        resolver = ToolAccessResolver()
        _file(resolver, replace=False, servers={"a": DENY_X})
        assert _denies(resolver, "a", "x")  # cached now

        _file(resolver, replace=True, servers={"a": DENY_Y})

        assert not _denies(resolver, "a", "x")
        assert _denies(resolver, "a", "y")

    def test_every_scope_the_file_dropped_is_lifted(self) -> None:
        resolver = ToolAccessResolver()
        _file(
            resolver,
            replace=False,
            servers={"a": DENY_X},
            groups={"g": DENY_X},
            members={("g", "m"): DENY_X},
            tenants={("a", "tenant:a"): DENY_X},
        )
        assert len(resolver.iter_registered_policies()) == 4

        _file(resolver, replace=True)

        assert resolver.iter_registered_policies() == []
        assert resolver._member_mcp_server_mapping == {}

    def test_a_first_load_adds_to_what_is_there(self) -> None:
        resolver = ToolAccessResolver()
        _file(resolver, replace=False, servers={"a": DENY_X}, members={("g", "m"): DENY_X})

        _file(resolver, replace=False, servers={"b": DENY_X}, members={("g", "n"): DENY_X})

        assert _denies(resolver, "a", "x") and _denies(resolver, "b", "x")
        assert resolver._member_mcp_server_mapping == {("g", "m"): "m", ("g", "n"): "n"}

    def test_the_topology_mode_is_not_taken_from_the_file(self) -> None:
        resolver = ToolAccessResolver()
        resolver.set_topology_mode("front_door")

        _file(resolver, replace=True, servers={"a": DENY_X})

        assert resolver.topology_mode == "front_door"
        assert not resolver.resolve_effective_policy("a").is_tool_allowed("anything")


class TestAPolicySetAtRuntimeAcrossAReload:
    def test_a_runtime_policy_on_a_scope_the_file_does_not_define_is_kept(self) -> None:
        resolver = ToolAccessResolver()
        _file(resolver, replace=False, servers={"a": DENY_X})
        resolver.set_mcp_server_policy("_global", DENY_Y)  # the agent's `_global` policy
        resolver.set_group_policy("g", DENY_Y)  # the REST endpoint's group scope

        _file(resolver, replace=True, servers={"a": DENY_X})

        assert resolver.get_configured_policy("provider", "_global") == DENY_Y
        assert resolver.get_configured_policy("group", "g") == DENY_Y
        assert _denies(resolver, "a", "y"), "`_global` still merges into every server"

    def test_the_file_replaces_a_runtime_policy_on_the_same_scope(self) -> None:
        resolver = ToolAccessResolver()
        resolver.set_mcp_server_policy("a", DENY_Y)

        _file(resolver, replace=True, servers={"a": DENY_X})

        assert resolver.get_configured_policy("provider", "a") == DENY_X

    def test_a_runtime_overwrite_of_a_file_policy_is_the_callers_to_keep(self) -> None:
        """Set by the file, then over REST: the entry is the caller's now."""
        resolver = ToolAccessResolver()
        _file(resolver, replace=False, servers={"a": DENY_X}, tenants={("a", "tenant:a"): DENY_X})
        resolver.set_mcp_server_policy("a", DENY_Y)
        resolver.set_standalone_member_policy("a", "tenant:a", DENY_Y)

        _file(resolver, replace=True)

        assert resolver.get_configured_policy("provider", "a") == DENY_Y
        assert resolver._standalone_member_policies[("a", "tenant:a", "tool")] == DENY_Y

    def test_a_member_policy_set_again_at_runtime_is_kept(self) -> None:
        resolver = ToolAccessResolver()
        _file(resolver, replace=False, members={("g", "m"): DENY_X})
        resolver.remove_member_policy("g", "m")
        resolver.set_member_policy("g", "m", DENY_Y, mcp_server_id="m")

        _file(resolver, replace=True)

        assert resolver.get_configured_policy("member", "g:m") == DENY_Y
        assert resolver._member_mcp_server_mapping == {("g", "m"): "m"}

    def test_a_runtime_removal_forgets_that_the_file_set_the_entry(self) -> None:
        resolver = ToolAccessResolver()
        _file(resolver, replace=False, servers={"a": DENY_X}, groups={"g": DENY_X}, tenants={("a", "t"): DENY_X})
        resolver.remove_mcp_server_policy("a")
        resolver.remove_group_policy("g")

        assert resolver._config_keys == {
            "mcp_server": set(),
            "group": set(),
            "member": set(),
            "standalone_member": set(),
        }


class TestClearingAndResetting:
    def test_clear_all_forgets_the_files_entries_too(self) -> None:
        resolver = ToolAccessResolver()
        _file(resolver, replace=False, servers={"a": DENY_X}, members={("g", "m"): DENY_X})

        resolver.clear_all()

        assert all(not keys for keys in resolver._config_keys.values())
        assert resolver._config_mapping_keys == set()
