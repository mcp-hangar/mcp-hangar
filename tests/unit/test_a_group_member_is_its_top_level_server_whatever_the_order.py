"""A group member that names a top-level server is that server, whatever the order in the file (#1437).

`build_config` built the entries in file order, and a member was looked up only
among the servers built so far. A group listed before its member's server built
the member from the member entry alone -- with only an `id`, a server with no
command -- and the repository held a different server under that id. Every
top-level server is now built before any group.

Driven through `build_config` and `load_config`, the seam a file, a config dict
and a reload all go through. The reload versions, through the real reload
handler, are in `test_a_reload_applies_the_whole_configuration.py`.
"""

from __future__ import annotations

from typing import Any

import pytest
from structlog.testing import capture_logs

from mcp_hangar.application.read_models.tool_projection import reset_tool_projection_registry
from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.server import config as server_config
from mcp_hangar.server.config import _StagedConfig, build_config, load_config
from mcp_hangar.server.state import get_runtime, GROUPS

GROUP = "pool"
MEMBER = "m1"
#: Every server id a test here adds, so teardown removes exactly those.
IDS = (MEMBER, "m2", "solo")
TOP_LEVEL = {"mode": "subprocess", "command": ["python", "-m", "some.server"], "description": "top-level"}
INLINE_COMMAND = ["python", "-m", "inline.server"]


def _group(*members: dict[str, Any]) -> dict[str, Any]:
    return {"mode": "group", "auto_start": False, "members": list(members) or [{"id": MEMBER, "weight": 3}]}


def _reset() -> None:
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    server_config._BUILT_FROM.clear()
    repository = get_runtime().repository
    for mcp_server_id in IDS:
        if repository.exists(mcp_server_id):
            repository.remove(mcp_server_id)


@pytest.fixture(autouse=True)
def _clean():
    original_groups = dict(GROUPS)
    GROUPS.clear()
    _reset()
    yield
    _reset()
    GROUPS.clear()
    GROUPS.update(original_groups)


def _member(member_id: str = MEMBER) -> McpServer:
    member = GROUPS[GROUP].get_member(member_id)
    assert member is not None
    return member.mcp_server


def _snapshot(staged: _StagedConfig) -> dict[str, Any]:
    group = staged.groups[GROUP]
    member = group.get_member(MEMBER)
    assert member is not None
    return {
        "servers": sorted(staged.servers),
        "member_is_the_server": member.mcp_server is staged.servers[MEMBER],
        "server": staged.servers[MEMBER].to_config_dict(),
        "group": group.to_config_dict(),
    }


class TestAMemberNamingATopLevelServer:
    @pytest.mark.parametrize(
        "order", [(GROUP, MEMBER), (MEMBER, GROUP)], ids=["group_before_server", "server_before_group"]
    )
    def test_is_the_repository_server_with_its_top_level_settings(self, order: tuple[str, str]) -> None:
        specs = {GROUP: _group(), MEMBER: TOP_LEVEL}

        with capture_logs() as logs:
            load_config({key: specs[key] for key in order})

        server = _member()
        assert server is get_runtime().repository.get(MEMBER)
        assert server.to_config_dict()["command"] == TOP_LEVEL["command"]
        assert server.description == "top-level"
        member = GROUPS[GROUP].get_member(MEMBER)
        assert member is not None and member.weight == 3, "the member entry's own keys still apply"
        assert not [entry for entry in logs if entry["event"] == "group_member_entry_settings_ignored"]

    def test_swapping_the_group_and_the_server_gives_identical_results(self) -> None:
        group_first = _snapshot(build_config({GROUP: _group(), MEMBER: TOP_LEVEL, "solo": TOP_LEVEL}))
        server_first = _snapshot(build_config({"solo": TOP_LEVEL, MEMBER: TOP_LEVEL, GROUP: _group()}))

        assert group_first == server_first
        assert group_first["member_is_the_server"] is True
        assert group_first["server"]["command"] == TOP_LEVEL["command"]

    def test_an_inline_entry_does_not_make_a_second_copy_of_a_later_server(self) -> None:
        """The inline copy was built first and replaced in the repository, so no reload ever stopped it."""
        inline = {"id": MEMBER, "mode": "subprocess", "command": INLINE_COMMAND, "weight": 2}

        with capture_logs() as logs:
            load_config({GROUP: _group(inline), MEMBER: TOP_LEVEL})

        server = _member()
        assert server is get_runtime().repository.get(MEMBER)
        assert server.to_config_dict()["command"] == TOP_LEVEL["command"]
        member = GROUPS[GROUP].get_member(MEMBER)
        assert member is not None and member.weight == 2
        (ignored,) = [entry for entry in logs if entry["event"] == "group_member_entry_settings_ignored"]
        assert (ignored["group_id"], ignored["member_id"], ignored["ignored"]) == (GROUP, MEMBER, ["command", "mode"])


class TestAnInlineOnlyMember:
    def test_is_built_from_its_entry(self) -> None:
        load_config({GROUP: _group({"id": "m2", "mode": "subprocess", "command": INLINE_COMMAND}), "solo": TOP_LEVEL})

        server = _member("m2")
        assert server is get_runtime().repository.get("m2")
        assert server.to_config_dict()["command"] == INLINE_COMMAND

    def test_whatever_the_order(self) -> None:
        inline = {"id": "m2", "command": INLINE_COMMAND}
        group_first = build_config({GROUP: _group(inline), "solo": TOP_LEVEL})
        server_first = build_config({"solo": TOP_LEVEL, GROUP: _group(inline)})

        for staged in (group_first, server_first):
            member = staged.groups[GROUP].get_member("m2")
            assert member is not None and member.mcp_server is staged.servers["m2"]
            assert staged.servers["m2"].to_config_dict()["command"] == INLINE_COMMAND
        assert sorted(group_first.servers) == sorted(server_first.servers) == ["m2", "solo"]


class TestAMemberNamingNoServer:
    @pytest.mark.parametrize(
        "entry",
        [{"id": MEMBER}, {"id": MEMBER, "mode": "remote"}, {"id": MEMBER, "mode": "docker", "weight": 2}],
        ids=["id_only", "remote_without_endpoint", "docker_without_image"],
    )
    def test_fails_the_load_naming_the_group_and_the_member(self, entry: dict[str, Any]) -> None:
        with pytest.raises(ConfigurationError, match=r"Group 'pool' member 'm1' names no server"):
            load_config({GROUP: _group(entry), "solo": TOP_LEVEL})

        assert GROUP not in GROUPS
        assert get_runtime().repository.get("solo") is None, "a refused load puts nothing in force"

    def test_a_member_naming_a_server_declared_later_in_another_group_is_not_refused(self) -> None:
        """Only an id no entry declares is refused: another group's inline member is a declared server."""
        inline = {"id": "m2", "command": INLINE_COMMAND}

        staged = build_config({GROUP: _group(inline), "other": _group({"id": "m2"})})

        first, second = (staged.groups[group_id].get_member("m2") for group_id in (GROUP, "other"))
        assert first is not None and second is not None
        assert first.mcp_server is second.mcp_server is staged.servers["m2"]
