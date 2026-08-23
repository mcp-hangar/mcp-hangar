"""`approval_list` is refused for the kinds no approval path serves (#1042, #1043).

#1028 gave prompts and resources the tool policy surface and said in the loader's
own docstring -- and in the 2.13.0 release notes -- that they inherit "the
approval gate" along with the merge semantics. They did not. `requires_approval()`
has exactly one decision consumer, the tool call path, and neither the prompts
proxy nor the resources projection references approvals at all, so an
approval-listed prompt or resource was listed and served immediately: no hold, no
human, no metric.

The startup reachability check, meanwhile, read the same `approval_list` off
*every* kind and refused the boot over it. One configuration, fail-open at
request time and fail-closed at boot, disagreeing with itself.

So the config is refused where it is written, in the shape #902 established for a
pin no caller can match, and the reachability check is told which kind it is
asking about. Whether a hold belongs on `resources/read` / `prompts/get` at all is
#1045; the refusal says "not supported", not "invalid", so that decision stays
open.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.model.mcp_server_config import ToolAccessPolicy
from mcp_hangar.domain.services.tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.server.bootstrap.reachability import collect_subsystem_requirements
from mcp_hangar.server.config import ServerConfigLoader, load_config, load_config_from_file

_SERVER = "docs_server"


def _spec(access: dict) -> dict:
    return {_SERVER: {"mode": "subprocess", "command": ["/bin/true"], "access": access}}


@pytest.fixture(autouse=True)
def _clean_resolver():
    reset_tool_access_resolver()
    yield
    reset_tool_access_resolver()


class TestTheConfigIsRefusedWhereItIsWritten:
    @pytest.mark.parametrize("kind", ["prompt", "resource"])
    def test_an_approval_list_on_a_non_tool_kind_refuses_the_load(self, kind: str) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            load_config(_spec({kind: {"approval_list": ["secret://*"]}}))

        message = str(excinfo.value)
        assert f"access.{kind}.approval_list" in message, "the operator must be told which key to edit"
        assert kind in message and "tool calls only" in message
        assert _SERVER in message

    @pytest.mark.parametrize("kind", ["prompt", "resource"])
    def test_the_policy_is_not_registered_by_the_refused_load(self, kind: str) -> None:
        """Refused means absent, not 'registered and then complained about'."""
        with pytest.raises(ConfigurationError):
            load_config(_spec({kind: {"approval_list": ["secret://*"]}}))

        assert get_tool_access_resolver().iter_registered_policies() == []

    def test_the_second_entry_point_refuses_it_too(self) -> None:
        """`ServerConfigLoader.apply_mcp_servers` is the reload path (#838)."""
        with pytest.raises(ConfigurationError, match="access.resource.approval_list"):
            ServerConfigLoader().apply_mcp_servers(_spec({"resource": {"approval_list": ["secret://*"]}}))

    def test_a_config_file_carrying_it_refuses_too(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump({"mcp_servers": _spec({"prompt": {"approval_list": ["draft_*"]}})}))

        with pytest.raises(ConfigurationError, match="access.prompt.approval_list"):
            load_config(load_config_from_file(str(path))["mcp_servers"])

    def test_a_group_carrying_it_refuses_too(self) -> None:
        with pytest.raises(ConfigurationError, match="access.resource.approval_list"):
            load_config(
                {
                    "member_1": {"mode": "subprocess", "command": ["/bin/true"]},
                    "group_g": {
                        "mode": "group",
                        "members": [{"id": "member_1"}],
                        "access": {"resource": {"approval_list": ["secret://*"]}},
                    },
                }
            )

    def test_a_per_tenant_block_carrying_it_refuses_too(self) -> None:
        with pytest.raises(ConfigurationError, match="access.prompt.approval_list"):
            load_config(
                {
                    _SERVER: {
                        "mode": "subprocess",
                        "command": ["/bin/true"],
                        "tool_access": {"member": {"tenant:a": {"access": {"prompt": {"approval_list": ["draft_*"]}}}}},
                    }
                }
            )


class TestWhatKeepsWorking:
    @pytest.mark.parametrize("key", ["allow_list", "deny_list"])
    @pytest.mark.parametrize("kind", ["prompt", "resource"])
    def test_allow_and_deny_still_register_for_both_kinds(self, kind: str, key: str) -> None:
        load_config(_spec({kind: {key: ["secret://*"]}}))

        registered = dict(get_tool_access_resolver().iter_registered_policies())
        assert f"mcp_server:{_SERVER}[{kind}]" in registered

    def test_a_tool_approval_list_is_untouched(self) -> None:
        load_config(
            {_SERVER: {"mode": "subprocess", "command": ["/bin/true"], "tools": {"approval_list": ["transfer"]}}}
        )

        registered = dict(get_tool_access_resolver().iter_registered_policies())
        assert registered[f"mcp_server:{_SERVER}"].approval_list == ("transfer",)


class TestTheKindFilterCoversEveryScope:
    """All four policy stores, filtered and unfiltered.

    The reachability check asks for one kind; the inventory view (no filter) is
    what a policy dump reads. Both shapes are pinned here so a scope cannot be
    dropped from one and not the other.
    """

    def _register_one_of_each(self) -> None:
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy("srv", ToolAccessPolicy(deny_list=("a",)))
        resolver.set_mcp_server_policy("srv", ToolAccessPolicy(deny_list=("b",)), kind="prompt")
        resolver.set_group_policy("grp", ToolAccessPolicy(deny_list=("c",)), kind="resource")
        resolver.set_member_policy(group_id="grp", member_id="tenant:a", policy=ToolAccessPolicy(deny_list=("d",)))
        resolver.set_standalone_member_policy("srv", "tenant:b", ToolAccessPolicy(deny_list=("e",)), kind="prompt")

    def test_no_filter_returns_every_scope_and_kind(self) -> None:
        self._register_one_of_each()

        scopes = {scope for scope, _policy in get_tool_access_resolver().iter_registered_policies()}

        assert scopes == {
            "mcp_server:srv",
            "mcp_server:srv[prompt]",
            "group:grp[resource]",
            "group:grp:member:tenant:a",
            "mcp_server:srv:member:tenant:b[prompt]",
        }

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            ("tool", {"mcp_server:srv", "group:grp:member:tenant:a"}),
            ("prompt", {"mcp_server:srv[prompt]", "mcp_server:srv:member:tenant:b[prompt]"}),
            ("resource", {"group:grp[resource]"}),
        ],
    )
    def test_a_filter_returns_only_that_kind(self, kind: str, expected: set[str]) -> None:
        self._register_one_of_each()

        scopes = {scope for scope, _p in get_tool_access_resolver().iter_registered_policies(kind=kind)}  # type: ignore[arg-type]

        assert scopes == expected


class TestTheStartupCheckAsksAboutTheKindItCanEnforce:
    """A policy reaching the resolver another way (REST, replay) must not refuse the boot."""

    def _context(self, *, gate: object | None) -> object:
        from types import SimpleNamespace

        return SimpleNamespace(approval_gate=gate, governed_task_store=None)

    @pytest.mark.parametrize("kind", ["prompt", "resource"])
    def test_a_non_tool_approval_policy_demands_nothing(self, kind: str) -> None:
        get_tool_access_resolver().set_mcp_server_policy(
            _SERVER, ToolAccessPolicy(approval_list=("secret://*",)), kind=kind
        )

        requirements = collect_subsystem_requirements({"relay_tasks_enabled": False}, self._context(gate=None))

        assert requirements == [], "no path holds a prompt or resource, so no gate is required"

    def test_a_tool_approval_policy_still_demands_the_gate(self) -> None:
        """The #678 regression this check exists for stays covered."""
        get_tool_access_resolver().set_mcp_server_policy("payments", ToolAccessPolicy(approval_list=("transfer",)))

        requirements = collect_subsystem_requirements({"relay_tasks_enabled": False}, self._context(gate=None))

        assert [(r.subsystem, r.fail_closed) for r in requirements] == [("approval_gate", True)]
        assert requirements[0].required_by == "tools.approval_list on mcp_server:payments"

    def test_the_delivery_check_is_kind_aware_too(self) -> None:
        """A silent channel must not be demanded for a policy nothing delivers."""
        get_tool_access_resolver().set_mcp_server_policy(
            _SERVER, ToolAccessPolicy(approval_list=("secret://*",)), kind="resource"
        )

        requirements = collect_subsystem_requirements(
            {"relay_tasks_enabled": False, "approvals": {"channel": "noop"}}, self._context(gate=object())
        )

        assert requirements == []
