"""`ToolAction` and `PolicyMode` must keep their wire values under `StrEnum`.

Both were `class X(str, Enum)` and are now `StrEnum`. The two are equal for
comparison and for JSON, and differ in exactly one place: `str()` and f-string
formatting. Under `str, Enum` those produced `'ToolAction.DENY'`; under
`StrEnum` they produce `'deny'`.

Nothing in the tree relied on the old form -- every call site uses `.value`
explicitly or an identity comparison -- so the conversion is behaviour-preserving
where it counts. These tests pin *where it counts*, so a future change back, or
a rename of a member, cannot quietly alter what goes on the wire.

The values themselves are contract, not decoration: `PolicyMode` is spelled in
the CRD's capitalised form (`Audit`/`Enforce`) while `ToolAction` is lower-case,
and the operator compiles `MCPEgressPolicy` objects against exactly those
strings.
"""

import json

import pytest

from mcp_hangar.domain.policies.egress_l7 import L7Policy, PolicyMode, ToolAction


class TestWireValuesAreUnchanged:
    @pytest.mark.parametrize(
        "member,expected",
        [
            (ToolAction.ALLOW, "allow"),
            (ToolAction.DENY, "deny"),
            (ToolAction.REQUIRE_APPROVAL, "require_approval"),
            (PolicyMode.AUDIT, "Audit"),
            (PolicyMode.ENFORCE, "Enforce"),
        ],
    )
    def test_value(self, member, expected):
        assert member.value == expected

    @pytest.mark.parametrize(
        "member,expected",
        [(ToolAction.DENY, "deny"), (PolicyMode.AUDIT, "Audit")],
    )
    def test_json_serialises_to_the_wire_value(self, member, expected):
        """The audit events and the operator payload both go out as JSON."""
        assert json.loads(json.dumps({"k": member}))["k"] == expected

    def test_equality_with_plain_strings_still_holds(self):
        """Call sites compare against literals in places; that must not break."""
        assert ToolAction.DENY == "deny"
        assert PolicyMode.ENFORCE == "Enforce"

    def test_lookup_by_value_still_holds(self):
        assert ToolAction("deny") is ToolAction.DENY
        assert PolicyMode("Audit") is PolicyMode.AUDIT


class TestStrEnumFormattingIsTheValue:
    """The one thing StrEnum changed, pinned so it is a decision not an accident."""

    def test_str_is_the_value_not_the_member_name(self):
        assert str(ToolAction.DENY) == "deny"
        assert f"{PolicyMode.AUDIT}" == "Audit"


class TestPolicyRoundTripsThroughTheWireForm:
    def test_from_dict_preserves_both_vocabularies(self):
        policy = L7Policy.from_dict(
            {
                "tools": {"deny": ["drop_*"]},
                "defaultAction": "Deny",
                "mode": "Audit",
            }
        )
        assert policy.default_action.value == "deny"
        assert policy.mode.value == "Audit"

    def test_mode_absent_defaults_to_enforce(self):
        """Fail-closed: a mode-less payload from an older operator keeps blocking."""
        assert L7Policy.from_dict({"defaultAction": "Deny"}).mode is PolicyMode.ENFORCE
