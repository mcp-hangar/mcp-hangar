"""`tool_projection.new_tools` in the registry and the config (#1801)."""

from __future__ import annotations

import pytest

from mcp_hangar.application.read_models.tool_projection import ToolProjectionRegistry
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.access_decision import decide
from mcp_hangar.domain.services.tool_access_resolver import ToolAccessResolver
from mcp_hangar.domain.value_objects import ToolDigest
from mcp_hangar.domain.value_objects.access import Caller, Target
from mcp_hangar.server import config as config_module
from mcp_hangar.server.config import ConfigurationError, _register_tool_projection_block

SHA = "a" * 64


def _registry(*tools: str) -> ToolProjectionRegistry:
    registry = ToolProjectionRegistry()
    registry.build_from_tools("srv", [ToolSchema(name=t, description="", input_schema={}) for t in tools])
    return registry


def _pin(registry: ToolProjectionRegistry, tool: str, tenant: str | None = None) -> None:
    registry.set_config_pin("srv", tool, tenant, ToolDigest(tool_name=tool, sha256=SHA))


class TestWhatIsWithheld:
    def test_a_tool_outside_the_pins_is_withheld(self):
        registry = _registry("old", "new")
        _pin(registry, "old")
        registry.set_new_tools_policy("srv", "withhold")

        assert registry.is_unadmitted("srv", "new")
        assert not registry.is_unadmitted("srv", "old")
        assert registry.is_withdrawn("srv", "new")

    def test_resolve_reads_it_withdrawn(self):
        registry = _registry("old", "new")
        _pin(registry, "old")
        registry.set_new_tools_policy("srv", "withhold")

        assert registry.resolve("srv", "new", None).is_withdrawn_for(None)
        assert not registry.resolve("srv", "old", None).is_withdrawn_for(None)

    def test_a_tenant_pin_admits_it_for_that_tenant_only(self):
        registry = _registry("old", "new")
        _pin(registry, "old")
        _pin(registry, "new", tenant="t1")
        registry.set_new_tools_policy("srv", "withhold")

        assert not registry.is_unadmitted("srv", "new", "t1")
        assert registry.is_unadmitted("srv", "new", "t2")
        assert registry.is_unadmitted("srv", "new", None)
        assert not registry.resolve("srv", "new", "t1").is_withdrawn_for("t1")
        assert registry.resolve("srv", "new", "t2").is_withdrawn_for("t2")

    def test_a_server_without_pins_has_no_baseline(self):
        registry = _registry("old", "new")
        registry.set_new_tools_policy("srv", "withhold")

        assert not registry.is_unadmitted("srv", "new")

    def test_the_policy_is_per_server(self):
        registry = _registry("old", "new")
        _pin(registry, "old")
        registry.set_new_tools_policy("other", "withhold")

        assert not registry.is_unadmitted("srv", "new")

    def test_allow_turns_it_off_again(self):
        registry = _registry("old", "new")
        _pin(registry, "old")
        registry.set_new_tools_policy("srv", "withhold")
        registry.set_new_tools_policy("srv", "allow")

        assert not registry.is_unadmitted("srv", "new")
        assert registry.new_tools_policy("srv") == "allow"

    def test_an_unknown_tool_stays_unknown(self):
        registry = _registry("old")
        _pin(registry, "old")
        registry.set_new_tools_policy("srv", "withhold")

        assert registry.resolve("srv", "never-seen", None) is None

    def test_a_prompt_or_resource_is_never_withheld_for_this(self):
        registry = _registry("old")
        _pin(registry, "old")
        registry.set_new_tools_policy("srv", "withhold")

        assert not registry.is_withdrawn("srv", "a-prompt", kind="prompt")

    def test_a_bad_policy_is_refused(self):
        with pytest.raises(ValueError, match="withhold"):
            ToolProjectionRegistry().set_new_tools_policy("srv", "maybe")


class TestTheDecisionReadsItBeforeTheCatalogueHasIt:
    """`withdrawal_status` is what `decide()` and the call gates read: an unadmitted tool is withdrawn uncatalogued."""

    def test_a_tool_not_yet_catalogued_is_withdrawn_when_the_pins_do_not_admit_it(self):
        registry = _registry("old")
        _pin(registry, "old")
        registry.set_new_tools_policy("srv", "withhold")

        assert registry.withdrawal_status("srv", "not-yet-seen", None) is True
        assert registry.withdrawal_status("srv", "not-yet-seen", "t1") is True
        assert registry.withdrawal_status("srv", "old", None) is False

    def test_a_tenant_pin_admits_it_uncatalogued_for_that_tenant_only(self):
        registry = ToolProjectionRegistry()
        _pin(registry, "old")
        _pin(registry, "new", tenant="t1")
        registry.set_new_tools_policy("srv", "withhold")

        assert registry.withdrawal_status("srv", "new", "t1") is None
        assert registry.withdrawal_status("srv", "new", "t2") is True

    @pytest.mark.parametrize("policy", [None, "allow"])
    def test_without_withhold_an_uncatalogued_tool_stays_unknown(self, policy):
        registry = _registry("old")
        _pin(registry, "old")
        if policy is not None:
            registry.set_new_tools_policy("srv", policy)

        assert registry.withdrawal_status("srv", "not-yet-seen", None) is None

    def test_a_group_that_withholds_withdraws_it_under_the_group_id(self):
        """A group's projections live under its members, so under the group id every tool is uncatalogued."""
        registry = _registry("old", "new")
        registry.set_config_pin("grp", "old", None, ToolDigest(tool_name="old", sha256=SHA))
        registry.set_new_tools_policy("grp", "withhold")

        assert registry.withdrawal_status("grp", "new", None) is True
        assert registry.withdrawal_status("grp", "old", None) is None
        assert registry.withdrawal_status("srv", "new", None) is False

    def test_the_call_decision_refuses_it_through_the_group_and_its_member(self):
        registry = _registry("old", "new")
        registry.set_config_pin("grp", "old", None, ToolDigest(tool_name="old", sha256=SHA))
        registry.set_new_tools_policy("grp", "withhold")
        resolver = ToolAccessResolver()

        for target in (Target.group("grp", member_server="srv"), Target.server("srv", owning_groups=("grp",))):
            assert decide(target, "new", Caller(), resolver=resolver, withdrawals=registry).withdrawn, target
            assert decide(target, "old", Caller(), resolver=resolver, withdrawals=registry).allowed, target
        assert decide(Target.server("srv"), "new", Caller(), resolver=resolver, withdrawals=registry).allowed

    def test_without_pins_an_uncatalogued_tool_stays_unknown(self):
        registry = _registry("old")
        registry.set_new_tools_policy("srv", "withhold")

        assert registry.withdrawal_status("srv", "not-yet-seen", None) is None


class TestAReloadReplacesIt:
    def test_removing_the_key_from_the_file_restores_the_default(self):
        live = _registry("old", "new")
        staged = ToolProjectionRegistry()
        staged.set_new_tools_policy("srv", "withhold")
        live.adopt_config_overlays(staged, replace=True)
        assert live.new_tools_policy("srv") == "withhold"

        live.adopt_config_overlays(ToolProjectionRegistry(), replace=True)

        assert live.new_tools_policy("srv") == "allow"

    def test_clearing_the_pins_clears_it(self):
        registry = _registry("old")
        registry.set_new_tools_policy("srv", "withhold")

        registry.clear_config_pins()

        assert registry.new_tools_policy("srv") == "allow"


class TestTheConfig:
    def _register(self, block: dict) -> ToolProjectionRegistry:
        registry = ToolProjectionRegistry()
        original = config_module._staged_config
        try:
            config_module._staged_config = lambda: type("S", (), {"projections": registry})()  # type: ignore[assignment]
            _register_tool_projection_block("srv", block)
        finally:
            config_module._staged_config = original  # type: ignore[assignment]
        return registry

    def test_withhold_is_read(self):
        assert self._register({"new_tools": "withhold"}).new_tools_policy("srv") == "withhold"

    def test_nothing_set_means_allow(self):
        assert self._register({"pins": {}}).new_tools_policy("srv") == "allow"

    @pytest.mark.parametrize("bad", ["block", "", 1, True, ["withhold"]])
    def test_anything_else_is_refused_by_name(self, bad):
        with pytest.raises(ConfigurationError, match="tool_projection.new_tools"):
            self._register({"new_tools": bad})
