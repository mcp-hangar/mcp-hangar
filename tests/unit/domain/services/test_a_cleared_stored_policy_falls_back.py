"""A cleared stored policy leaves what a restart would put there (#2178).

A restart adopts the file's policies and then replays the stored rows. With a
row cleared, the scope holds the file's policy when the file declares one, and
the file owns it again; otherwise it holds the runtime policy the stored one
covered, or none. A clear never touches a scope no stored policy holds.
"""

from __future__ import annotations

import pytest

from mcp_hangar.auth.policy_projection import apply_stored_policy
from mcp_hangar.domain.services.tool_access_resolver import ToolAccessResolver
from mcp_hangar.domain.value_objects import ToolAccessPolicy

FILE = ToolAccessPolicy(deny_list=("add",), approval_list=("pay",))
REST = ToolAccessPolicy(deny_list=("echo",))
HOT_LOADED = ToolAccessPolicy(deny_list=("drop",))


def _file(*, group: ToolAccessPolicy | None = FILE) -> ToolAccessResolver:
    staged = ToolAccessResolver()
    if group is not None:
        staged.set_group_policy("pool", group)
    return staged


def _booted(stored: dict[tuple[str, str], ToolAccessPolicy], *, file: ToolAccessResolver) -> ToolAccessResolver:
    """What startup builds: the file's policies, then the stored rows over them."""
    resolver = ToolAccessResolver()
    resolver.adopt_config_policies(file, replace=False)
    for (scope, target_id), policy in stored.items():
        apply_stored_policy(resolver, scope, target_id, policy)
    return resolver


def test_a_clear_falls_back_to_the_files_policy() -> None:
    resolver = _booted({}, file=_file())
    resolver.set_stored_policy("group", "pool", REST)
    assert resolver.get_configured_policy("group", "pool") == REST

    assert resolver.clear_stored_policy("group", "pool")

    assert resolver.get_configured_policy("group", "pool") == FILE


def test_a_clear_with_no_file_policy_leaves_none() -> None:
    resolver = _booted({}, file=_file(group=None))
    resolver.set_stored_policy("group", "pool", REST)

    assert resolver.clear_stored_policy("group", "pool")

    assert resolver.get_configured_policy("group", "pool") is None


@pytest.mark.parametrize("file_declares", [True, False])
def test_the_state_after_a_clear_is_the_state_after_a_restart(file_declares: bool) -> None:
    file = _file(group=FILE if file_declares else None)
    kept = {("provider", "other"): REST}
    live = _booted({("group", "pool"): REST, **kept}, file=file)

    live.clear_stored_policy("group", "pool")

    restarted = _booted(kept, file=file)
    assert live.iter_registered_policies() == restarted.iter_registered_policies()
    # And the file owns the scope again: a reload whose file drops it lifts it, as after a restart.
    for resolver in (live, restarted):
        resolver.adopt_config_policies(_file(group=None), replace=True)
    assert live.iter_registered_policies() == restarted.iter_registered_policies()
    assert live.get_configured_policy("group", "pool") is None


def test_a_clear_follows_the_file_a_reload_left() -> None:
    resolver = _booted({}, file=_file())
    resolver.set_stored_policy("group", "pool", REST)
    # A reload whose file no longer declares the scope, then the replay of the rows.
    with resolver.locked():
        resolver.adopt_config_policies(_file(group=None), replace=True)
        apply_stored_policy(resolver, "group", "pool", REST)

    resolver.clear_stored_policy("group", "pool")

    assert resolver.get_configured_policy("group", "pool") is None


def test_a_policy_no_stored_row_put_there_is_not_cleared() -> None:
    # A clear for a row that never existed must not lift what `hangar_load` set.
    resolver = ToolAccessResolver()
    resolver.set_mcp_server_policy("loaded", HOT_LOADED)

    assert not resolver.clear_stored_policy("provider", "loaded")

    assert resolver.get_configured_policy("provider", "loaded") == HOT_LOADED


def test_a_clear_gives_back_the_runtime_policy_the_stored_one_covered() -> None:
    resolver = ToolAccessResolver()
    resolver.set_mcp_server_policy("loaded", HOT_LOADED)
    resolver.set_stored_policy("provider", "loaded", REST)
    resolver.set_stored_policy("provider", "loaded", ToolAccessPolicy(deny_list=("other",)))

    resolver.clear_stored_policy("provider", "loaded")

    assert resolver.get_configured_policy("provider", "loaded") == HOT_LOADED


def test_a_runtime_write_over_a_stored_policy_is_not_taken_back_by_a_clear() -> None:
    resolver = ToolAccessResolver()
    resolver.set_stored_policy("provider", "loaded", REST)
    resolver.set_mcp_server_policy("loaded", HOT_LOADED)

    assert not resolver.clear_stored_policy("provider", "loaded")

    assert resolver.get_configured_policy("provider", "loaded") == HOT_LOADED


def test_a_second_clear_changes_nothing() -> None:
    resolver = _booted({}, file=_file())
    resolver.set_stored_policy("group", "pool", REST)
    resolver.clear_stored_policy("group", "pool")

    assert not resolver.clear_stored_policy("group", "pool")

    assert resolver.get_configured_policy("group", "pool") == FILE


@pytest.mark.parametrize(
    ("scope", "target_id", "read_as"),
    [
        ("provider", "math", ("provider", "math")),
        ("mcp_server", "math", ("provider", "math")),
        ("group", "pool", ("group", "pool")),
        ("member", "pool:math", ("member", "pool:math")),
        ("member", "math", ("member", "math:math")),
    ],
)
def test_a_stored_policy_is_addressed_as_the_endpoint_names_it(
    scope: str, target_id: str, read_as: tuple[str, str]
) -> None:
    resolver = ToolAccessResolver()

    resolver.set_stored_policy(scope, target_id, REST)
    assert resolver.get_configured_policy(*read_as) == REST
    resolver.clear_stored_policy(scope, target_id)
    assert resolver.get_configured_policy(*read_as) is None


def test_an_unknown_scope_is_ignored() -> None:
    resolver = ToolAccessResolver()

    resolver.set_stored_policy("tenant", "a", REST)

    assert resolver.iter_registered_policies() == []
    assert not resolver.clear_stored_policy("tenant", "a")
