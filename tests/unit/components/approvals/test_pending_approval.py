"""The pending-approval model serializes to an `inputRequests` value (WS-5).

Nothing here goes on the wire yet. The point of the model is that when it does
-- either because modelcontextprotocol#2919 lands, or because we serve it under
our own namespace -- the value drops into an `inputRequests` map unchanged.

The absence tested below matters more than the presence: **no
`requestedSchema`**. A value carrying one looks like an elicitation to a client
that does not know our method, and an elicitation is answerable by the caller --
which is exactly the party an approval gate exists to not trust.
"""

from __future__ import annotations

import datetime as dt

from mcp_hangar.approvals.pending import (
    APPROVAL_INPUT_METHOD,
    ApprovalPolicyBasis,
    ApprovalSubject,
    PendingApproval,
)

EXPIRES = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.UTC)


def _pending(**overrides) -> PendingApproval:
    kwargs = {
        "approval_id": "ap-1",
        "subject": ApprovalSubject(
            mcp_server_id="grafana",
            tool_name="delete_dashboard",
            arguments_hash="sha256:abc",
        ),
        "expires_at": EXPIRES,
    }
    kwargs.update(overrides)
    return PendingApproval(**kwargs)


class TestTheWireShape:
    def test_it_is_method_discriminated(self) -> None:
        value = _pending().to_input_request()

        assert value["method"] == APPROVAL_INPUT_METHOD
        assert value["method"] == "io.mcp-hangar/approval"

    def test_it_carries_no_requested_schema(self) -> None:
        """The load-bearing absence.

        With a schema, an unaware client renders an answerable prompt and the
        caller can answer its own approval. Without one it can display that
        something is pending and has nothing to fill in.
        """
        value = _pending().to_input_request()

        assert "requestedSchema" not in value
        assert "requestedSchema" not in value["params"]

    def test_it_carries_a_human_readable_message(self) -> None:
        value = _pending().to_input_request()

        assert "delete_dashboard" in value["message"]
        assert "grafana" in value["message"]

    def test_an_explicit_message_wins(self) -> None:
        value = _pending(message="Two-person rule: deleting a production dashboard").to_input_request()

        assert value["message"] == "Two-person rule: deleting a production dashboard"

    def test_the_value_is_json_serializable_as_is(self) -> None:
        """ "Insertable without transformation" is only true if it survives json."""
        import json

        value = _pending().to_input_request()

        assert json.loads(json.dumps(value)) == value

    def test_it_nests_under_input_requests_unchanged(self) -> None:
        """The acceptance, stated literally."""
        pending = _pending()

        input_requests = {pending.approval_id: pending.to_input_request()}

        assert input_requests["ap-1"]["method"] == APPROVAL_INPUT_METHOD


class TestTheSubject:
    def test_arguments_are_bound_by_hash_not_carried(self) -> None:
        """The subject travels to the approver; the arguments may not be theirs to see.

        The hash still binds the decision to one exact invocation, so approving
        this does not approve a different call to the same tool.
        """
        value = _pending().to_input_request()
        subject = value["params"]["subject"]

        assert subject["argumentsHash"] == "sha256:abc"
        assert "arguments" not in subject
        assert "arguments" not in value["params"]

    def test_it_names_the_server_and_tool(self) -> None:
        subject = _pending().to_input_request()["params"]["subject"]

        assert subject == {
            "mcpServer": "grafana",
            "tool": "delete_dashboard",
            "argumentsHash": "sha256:abc",
        }


class TestPolicyBasis:
    def test_it_is_omitted_when_empty(self) -> None:
        """An approval can be requested without a rule naming it."""
        assert "policyBasis" not in _pending().to_input_request()["params"]

    def test_it_is_included_when_present(self) -> None:
        value = _pending(policy_basis=ApprovalPolicyBasis(rule="l7:delete_*", reason="destructive")).to_input_request()

        assert value["params"]["policyBasis"] == {"rule": "l7:delete_*", "reason": "destructive"}


class TestAuthorization:
    def test_it_names_a_permission_rather_than_a_person(self) -> None:
        """Naming an individual would imply routing this model does not do.

        The gate authorizes on the permission (ADR-016); the model says which
        one, and stops there.
        """
        params = _pending().to_input_request()["params"]

        assert params["requiredPermission"] == "approval:resolve"


class TestFromExistingAggregate:
    def test_it_reuses_the_aggregate_hash(self) -> None:
        """Two hashes of the same arguments that disagree would be worse than none."""
        from datetime import timedelta

        from mcp_hangar.approvals.models import ApprovalRequest, ApprovalState

        now = dt.datetime.now(dt.UTC)
        request = ApprovalRequest(
            approval_id="ap-9",
            mcp_server_id="grafana",
            tool_name="delete_dashboard",
            arguments={"uid": "abc"},
            arguments_hash="sha256:precomputed",
            requested_at=now,
            expires_at=now + timedelta(minutes=5),
            state=ApprovalState.PENDING,
            channel="noop",
        )

        pending = PendingApproval.from_request(request)

        assert pending.subject.arguments_hash == "sha256:precomputed"
        assert pending.approval_id == "ap-9"
        assert pending.expires_at == request.expires_at
