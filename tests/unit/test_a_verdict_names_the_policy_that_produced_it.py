"""A verdict says which policy produced it, and which version of it (#1129).

`policy_id` existed as a documented audit field that nothing ever set and
nothing ever read -- the same class as the metrics that were defined and never
registered. Nothing else in a verdict carried policy identity either, so telling
two records apart across a policy change meant joining by timestamp against the
`EgressPolicySet` stream. That is a reconstruction, not a record.

Identity is a content hash of the compiled rules, because the two sources have
nothing else in common: an operator-compiled policy has a Kubernetes
`resourceVersion` upstream, and a policy from `config.yaml` or the REST channel
has no identity at all.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from mcp_hangar.domain.events.enforcement import EgressPolicySet, EgressPolicyViolationObserved
from mcp_hangar.domain.exceptions import EgressPolicyApprovalRequiredError, EgressPolicyDeniedError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.policies.egress_l7 import (
    ArgumentRules,
    evaluate,
    HeaderMatch,
    HeaderRules,
    L7Policy,
    PolicyMode,
    ToolRules,
)


class _Proceeded(Exception):
    """Raised from a stubbed ensure_ready: the call got past the L7 gate."""


def _server(policy: L7Policy) -> McpServer:
    server = McpServer(mcp_server_id="s", mode="subprocess", command=["echo"], l7_policy=policy)
    server.ensure_ready = Mock(side_effect=_Proceeded())  # type: ignore[method-assign]
    return server


class TestTheIdentity:
    def test_the_same_rules_produce_the_same_id(self) -> None:
        """Stable across restarts and replicas, which a resourceVersion is not."""
        first = L7Policy(tools=ToolRules(deny=("refund",)))
        second = L7Policy(tools=ToolRules(deny=("refund",)))

        assert first.policy_id == second.policy_id

    def test_changed_rules_change_the_id(self) -> None:
        base = L7Policy(tools=ToolRules(deny=("refund",)))

        assert L7Policy(tools=ToolRules(deny=("charge",))).policy_id != base.policy_id
        assert L7Policy(tools=ToolRules(deny=("refund",), allow=("*",))).policy_id != base.policy_id
        assert L7Policy(tools=ToolRules(deny=("refund",)), arguments=ArgumentRules(max_payload_bytes=1)).policy_id != (
            base.policy_id
        )
        assert (
            L7Policy(
                tools=ToolRules(deny=("refund",)),
                headers=HeaderRules(deny=(HeaderMatch("Mcp-Param-Region", ("eu-*",)),)),
            ).policy_id
            != base.policy_id
        )

    def test_flipping_audit_to_enforce_changes_the_id(self) -> None:
        """The mode is part of what the policy says. Two verdicts either side of
        the flip must not claim to come from the same policy."""
        audit = L7Policy(tools=ToolRules(allow=("*",)), mode=PolicyMode.AUDIT)
        enforce = L7Policy(tools=ToolRules(allow=("*",)), mode=PolicyMode.ENFORCE)

        assert audit.policy_id != enforce.policy_id

    def test_the_id_survives_the_wire_round_trip(self) -> None:
        """Two gateways given the same policy document agree on its id."""
        policy = L7Policy(
            tools=ToolRules(allow=("charge",), deny=("refund",)),
            arguments=ArgumentRules(secret_patterns=("aws-keys",), max_payload_bytes=1024),
            mode=PolicyMode.AUDIT,
        )

        parsed = L7Policy.from_dict(policy.to_wire())

        assert parsed.policy_id == policy.policy_id
        assert parsed == policy  # the id is derived, so it does not disturb equality

    def test_the_id_is_shaped_like_a_hash(self) -> None:
        policy_id = L7Policy().policy_id

        assert policy_id.startswith("sha256:")
        assert len(policy_id) == len("sha256:") + 16


class TestTheVerdictCarriesIt:
    def test_evaluate_names_the_policy(self) -> None:
        policy = L7Policy(tools=ToolRules(deny=("refund",)))

        assert evaluate("refund", {}, policy).policy_id == policy.policy_id

    def test_an_allow_names_it_too(self) -> None:
        """Not only refusals: an audit record of a permitted call is a record."""
        policy = L7Policy(tools=ToolRules(allow=("*",)))

        assert evaluate("charge", {}, policy).policy_id == policy.policy_id

    def test_a_deny_carries_it_to_the_caller_side_record(self) -> None:
        policy = L7Policy(tools=ToolRules(deny=("refund",)))

        with pytest.raises(EgressPolicyDeniedError) as excinfo:
            _server(policy).invoke_tool("refund", {})

        assert excinfo.value.policy_id == policy.policy_id
        assert excinfo.value.details["policy_id"] == policy.policy_id

    def test_an_approval_refusal_carries_it(self) -> None:
        policy = L7Policy(tools=ToolRules(require_approval=("refund",)))

        with pytest.raises(EgressPolicyApprovalRequiredError) as excinfo:
            _server(policy).invoke_tool("refund", {})

        assert excinfo.value.policy_id == policy.policy_id

    def test_an_audit_mode_observation_carries_it(self) -> None:
        """Audit mode is the whole point of the field: it is the mode whose only
        output IS the record."""
        policy = L7Policy(tools=ToolRules(deny=("refund",)), mode=PolicyMode.AUDIT)
        server = _server(policy)

        with pytest.raises(_Proceeded):  # Audit observes and lets the call through
            server.invoke_tool("refund", {})

        observed = [e for e in server.collect_events() if isinstance(e, EgressPolicyViolationObserved)]
        assert observed and observed[0].policy_id == policy.policy_id


class TestTheChangeStreamJoinsOnTheSameValue:
    def test_the_set_event_carries_the_id_the_verdicts_carry(self) -> None:
        """ "Which policy decided this call" and "when did that policy change"
        must join on a value, not on adjacent timestamps."""
        policy = L7Policy(tools=ToolRules(deny=("refund",)))

        event = EgressPolicySet(
            mcp_server_id="s",
            source="api",
            mode=policy.mode.value,
            default_action=policy.default_action.value,
            policy_id=policy.policy_id,
        )

        assert event.policy_id == evaluate("refund", {}, policy).policy_id


class TestTheEmptySlotIsGone:
    def test_policy_evaluation_result_no_longer_advertises_an_unfilled_field(self) -> None:
        """A documented-but-always-empty audit field is worse than an absent one:
        a reader reasonably assumes it works."""
        from mcp_hangar.domain.contracts import PolicyEvaluationResult

        assert not hasattr(PolicyEvaluationResult(allowed=True), "policy_id")
        with pytest.raises(TypeError):
            PolicyEvaluationResult.allow(reason="x", policy_id="sha256:whatever")  # type: ignore[call-arg]
