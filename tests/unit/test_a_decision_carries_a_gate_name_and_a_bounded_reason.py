"""`Decision` speaks the ADR-029 gate vocabulary, and the typed values refuse the shapes that caused #1763.

`Decision.gate` is a stage of the executor's `_GATES`, `Decision.reason` is a
code from `GATE_REASONS`, and `Decision.outcome` is what `hangar.gate.outcome`
spells; #1918 and the refusal logs read them from one place. `Target` and
`Caller` are validated at construction: a member server id can no longer land
in the tenant slot, because there is no slot it fits.
"""

from __future__ import annotations

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    ToolProjectionRegistry,
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.contracts import IWithdrawalOverlay
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services import access_decision
from mcp_hangar.domain.services.access_decision import decide, set_withdrawal_overlay_source, withdrawal_overlay
from mcp_hangar.domain.services.tool_access_resolver import DEFAULT_KIND, PolicyKind, ToolAccessResolver
from mcp_hangar.domain.value_objects import Caller, Decision, Target, ToolAccessPolicy
from mcp_hangar.domain.value_objects.access import (
    DECISION_REASONS,
    GATE_TOOL_ACCESS,
    GATE_WITHDRAWAL,
    OUTCOME_ALLOW,
    OUTCOME_DENY,
    REASON_TOOL_ACCESS,
    REASON_WITHDRAWN,
)
from mcp_hangar.observability.conventions import GATE_REASONS, Gate
from mcp_hangar.server.bootstrap.withdrawals import wire_withdrawal_overlay
from mcp_hangar.server.tools.batch.executor import _GATES


@pytest.fixture
def unwired() -> None:
    set_withdrawal_overlay_source(None)
    yield
    set_withdrawal_overlay_source(None)
    reset_tool_projection_registry()


class TestTheVocabularyIsTheExecutors:
    def test_the_domain_spells_the_vocabulary_as_the_conventions_do(self) -> None:
        """ADR-027 keeps the domain from importing `observability`, so the spelling is held equal here."""
        assert (GATE_TOOL_ACCESS, GATE_WITHDRAWAL) == (Gate.TOOL_ACCESS, Gate.WITHDRAWAL)
        assert (OUTCOME_ALLOW, OUTCOME_DENY) == (Gate.ALLOW, Gate.DENY)
        assert REASON_TOOL_ACCESS == GATE_REASONS["ToolAccessDeniedError"] == GATE_REASONS["ToolAccessDenied"]
        assert GATE_REASONS["ToolWithdrawnError"] == REASON_WITHDRAWN

    def test_every_gate_a_decision_can_name_is_a_stage_of_the_executor(self) -> None:
        stages = {gate.__name__.removeprefix("_gate_") for gate in _GATES}

        assert set(DECISION_REASONS) == {Gate.TOOL_ACCESS, Gate.WITHDRAWAL}
        assert set(DECISION_REASONS) <= stages

    def test_every_reason_is_the_code_the_executor_reports_for_that_refusal(self) -> None:
        assert DECISION_REASONS[Gate.TOOL_ACCESS] == GATE_REASONS["ToolAccessDeniedError"]
        assert DECISION_REASONS[Gate.WITHDRAWAL] == GATE_REASONS["ToolWithdrawnError"]
        assert set(DECISION_REASONS.values()) <= set(GATE_REASONS.values())

    def test_the_outcome_is_spelled_as_the_gate_event_spells_it(self) -> None:
        assert Decision.allow().outcome == Gate.ALLOW
        assert Decision.refused_by(Gate.WITHDRAWAL).outcome == Gate.DENY

    def test_a_refusal_carries_its_gate_and_that_gates_reason(self) -> None:
        refused = Decision.refused_by(Gate.TOOL_ACCESS)

        assert (refused.allowed, refused.gate, refused.reason) == (False, "tool_access", "tool_not_in_access_policy")
        assert not refused.withdrawn
        assert Decision.refused_by(Gate.WITHDRAWAL).withdrawn

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"allowed": True, "gate": Gate.WITHDRAWAL},
            {"allowed": True, "reason": "tool_withdrawn"},
            {"allowed": False},
            {"allowed": False, "gate": "digest_pin", "reason": "digest_mismatch"},
            {"allowed": False, "gate": Gate.TOOL_ACCESS, "reason": "tool_withdrawn"},
            {
                "allowed": False,
                "gate": Gate.TOOL_ACCESS,
                "reason": "tool_not_in_access_policy",
                "approval_policy": ToolAccessPolicy(),
            },
        ],
    )
    def test_a_decision_outside_the_vocabulary_cannot_be_built(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            Decision(**kwargs)

    def test_a_gate_outside_the_two_cannot_refuse(self) -> None:
        with pytest.raises(ValueError):
            Decision.refused_by("circuit_breaker")

    def test_an_approval_policy_is_observational(self) -> None:
        held = Decision.allow(approval_policy=ToolAccessPolicy(approval_list=("x",)))

        assert held.allowed and held.approval_policy is not None and held.gate is None


class TestATargetHasNoSlotATenantFits:
    def test_a_server_names_its_own_scope_then_one_per_owning_group(self) -> None:
        target = Target.server("m1", owning_groups=("g1", "g2"))

        assert target.policy_scopes() == (("m1", None, None), ("g1", "g1", "m1"), ("g2", "g2", "m1"))

    def test_a_group_names_one_scope_with_the_selected_member(self) -> None:
        assert Target.group("g1", member_server="m1").policy_scopes() == (("g1", "g1", "m1"),)
        assert Target.group("g1").policy_scopes() == (("g1", "g1", None),)

    @pytest.mark.parametrize(
        "build",
        [
            lambda: Target("", "server"),
            lambda: Target("s", "tenant"),  # type: ignore[arg-type]
            lambda: Target("s", "server", kind="widget"),  # type: ignore[arg-type]
            lambda: Target("s", "server", owning_groups=["g"]),  # type: ignore[arg-type]
            lambda: Target("s", "server", owning_groups=("g", "g")),
            lambda: Target("s", "server", owning_groups=("s",)),
            lambda: Target("s", "server", owning_groups=("",)),
            lambda: Target("g", "group", owning_groups=("h",)),
            lambda: Target("s", "server", member_server="m"),
            lambda: Target("g", "group", member_server="g"),
            lambda: Target("g", "group", member_server=""),
        ],
    )
    def test_a_target_that_is_not_a_shape_a_call_resolves_to_cannot_be_built(self, build) -> None:
        with pytest.raises(ValueError):
            build()

    def test_a_target_is_frozen(self) -> None:
        with pytest.raises(AttributeError):
            Target.server("s").addressed = "t"  # type: ignore[misc]

    def test_the_kinds_stay_importable_from_the_resolver(self) -> None:
        assert DEFAULT_KIND == "tool"
        assert PolicyKind is not None


class TestACallerCarriesItsTenantInOnePlace:
    def test_an_anonymous_caller_has_no_tenant(self) -> None:
        assert Caller().tenant_id is None
        assert Caller(tenant_id="tenant:a").tenant_id == "tenant:a"

    @pytest.mark.parametrize(
        "kwargs",
        [{"tenant_id": ""}, {"principal_id": ""}, {"roles": {"admin"}}, {"roles": frozenset({""})}],
    )
    def test_an_empty_identity_is_refused(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            Caller(**kwargs)


class TestTheOverlayIsWired:
    def test_deciding_before_anything_is_wired_raises_rather_than_refusing(self, unwired: None) -> None:
        with pytest.raises(RuntimeError, match="no withdrawal overlay is wired"):
            decide(Target.server("s"), "alpha", Caller(), resolver=ToolAccessResolver())

    def test_bootstrap_wires_the_registry_getter_so_a_reset_registry_is_still_read(self, unwired: None) -> None:
        wire_withdrawal_overlay()
        resolver = ToolAccessResolver()
        get_tool_projection_registry().withdraw("s", "alpha")

        assert withdrawal_overlay() is get_tool_projection_registry()
        assert decide(Target.server("s"), "alpha", Caller(), resolver=resolver).withdrawn

        reset_tool_projection_registry()

        assert decide(Target.server("s"), "alpha", Caller(), resolver=resolver).allowed

    def test_the_registry_is_the_overlay(self) -> None:
        assert isinstance(ToolProjectionRegistry(), IWithdrawalOverlay)

    def test_an_explicit_overlay_needs_no_wiring(self, unwired: None) -> None:
        registry = ToolProjectionRegistry()
        registry.set_config_withdrawal("s", "alpha", "tenant:a", kind="prompt")

        prompt = Target.server("s", kind="prompt")
        assert decide(
            prompt, "alpha", Caller(tenant_id="tenant:a"), resolver=ToolAccessResolver(), withdrawals=registry
        ).withdrawn
        assert decide(
            prompt, "alpha", Caller(tenant_id="tenant:b"), resolver=ToolAccessResolver(), withdrawals=registry
        ).allowed


class TestTheRegistryAnswersAsACallResolvesIt:
    def test_a_tool_the_catalogue_does_not_know_is_unknown_not_withdrawn(self) -> None:
        registry = ToolProjectionRegistry()

        assert registry.withdrawal_status("s", "alpha", None) is None
        assert registry.withdrawal_status("s", "alpha", None, kind="prompt") is False

    def test_a_discovered_tool_reads_its_status_and_its_tenant_overrides(self) -> None:
        registry = ToolProjectionRegistry()
        registry.build_from_tools(
            "s",
            [
                ToolSchema(name="alpha", description="d", input_schema={}),
                ToolSchema(name="beta", description="d", input_schema={}),
            ],
            status_overrides={"beta": "withdrawn"},
            tenant_overrides={"alpha": {"tenant:a": "withdrawn"}},
        )

        assert registry.withdrawal_status("s", "alpha", None) is False
        assert registry.withdrawal_status("s", "alpha", "tenant:a") is True
        assert registry.withdrawal_status("s", "beta", "tenant:b") is True

    def test_a_withdrawal_on_the_selected_member_withdraws_the_group_call(self) -> None:
        registry = ToolProjectionRegistry()
        registry.withdraw("m1", "alpha", "tenant:a")
        resolver = ToolAccessResolver()

        via_m1 = decide(
            Target.group("g", member_server="m1"),
            "alpha",
            Caller(tenant_id="tenant:a"),
            resolver=resolver,
            withdrawals=registry,
        )
        via_m2 = decide(
            Target.group("g", member_server="m2"),
            "alpha",
            Caller(tenant_id="tenant:a"),
            resolver=resolver,
            withdrawals=registry,
        )

        assert via_m1.withdrawn and via_m2.allowed


class TestTheGatesAskInOrder:
    def test_access_is_asked_before_withdrawal(self) -> None:
        resolver = ToolAccessResolver()
        resolver.set_mcp_server_policy("s", ToolAccessPolicy(deny_list=("alpha",)))
        registry = ToolProjectionRegistry()
        registry.withdraw("s", "alpha")

        refused = decide(Target.server("s"), "alpha", Caller(), resolver=resolver, withdrawals=registry)

        assert refused.gate == Gate.TOOL_ACCESS

    def test_a_member_of_a_group_that_denies_is_refused_when_named_directly(self) -> None:
        resolver = ToolAccessResolver()
        resolver.set_group_policy("g", ToolAccessPolicy(deny_list=("alpha",)))

        alone = decide(Target.server("m1"), "alpha", Caller(), resolver=resolver, withdrawals=ToolProjectionRegistry())
        owned = decide(
            Target.server("m1", owning_groups=("g",)),
            "alpha",
            Caller(),
            resolver=resolver,
            withdrawals=ToolProjectionRegistry(),
        )

        assert alone.allowed and owned.gate == Gate.TOOL_ACCESS

    def test_the_front_door_denies_a_caller_without_a_tenant(self) -> None:
        resolver = ToolAccessResolver()
        resolver.set_topology_mode("front_door")

        anonymous = decide(
            Target.server("s"), "alpha", Caller(), resolver=resolver, withdrawals=ToolProjectionRegistry()
        )
        known = decide(
            Target.server("s"),
            "alpha",
            Caller(tenant_id="tenant:a"),
            resolver=resolver,
            withdrawals=ToolProjectionRegistry(),
        )

        assert anonymous.reason == "tool_not_in_access_policy" and known.allowed

    def test_the_module_keeps_no_overlay_when_unwired(self, unwired: None) -> None:
        assert access_decision._overlay_source is None
