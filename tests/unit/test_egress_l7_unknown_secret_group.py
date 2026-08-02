"""A secret-pattern group this build does not know is a policy error.

``scan_arguments`` skipped unknown group names and its docstring said they
"should be caught by CRD validation". They are not: the MCPEgressPolicy CRD
declares ``spec...arguments.secretPatterns`` as ``items: {type: string}`` with a
``maxItems`` cap and no enum, so the API server accepts ``github-token``
(singular) and hands it straight through. The REST channel never touches the
CRD at all.

The consequence is the worst shape a policy bug can take: the policy is
accepted, reports as compiled and enforcing, and the detector its author asked
for is silently off. Rejecting at parse time is the only point where that is
visible to whoever wrote it.
"""

import pytest

from mcp_hangar.domain.policies.egress_l7 import (
    KNOWN_SECRET_PATTERN_GROUPS,
    ArgumentRules,
    L7Policy,
    scan_arguments,
)


class TestUnknownGroupIsRejectedAtParse:
    def test_single_unknown_group(self):
        with pytest.raises(ValueError) as excinfo:
            L7Policy.from_dict({"arguments": {"secretPatterns": ["github-token"]}})
        assert "github-token" in str(excinfo.value)

    def test_error_lists_the_known_groups(self):
        """A typo is only actionable if the message says what was meant."""
        with pytest.raises(ValueError) as excinfo:
            L7Policy.from_dict({"arguments": {"secretPatterns": ["jwtt"]}})
        message = str(excinfo.value)
        for group in KNOWN_SECRET_PATTERN_GROUPS:
            assert group in message

    def test_several_unknown_groups_are_all_named(self):
        with pytest.raises(ValueError) as excinfo:
            L7Policy.from_dict({"arguments": {"secretPatterns": ["nope", "alsonope", "jwt"]}})
        message = str(excinfo.value)
        assert "nope" in message
        assert "alsonope" in message

    def test_a_valid_group_alongside_an_invalid_one_still_rejects(self):
        """Partial acceptance would be the same silent-hole bug, smaller."""
        with pytest.raises(ValueError):
            L7Policy.from_dict({"arguments": {"secretPatterns": ["jwt", "github-token"]}})


class TestKnownGroupsStillParse:
    @pytest.mark.parametrize("group", sorted(KNOWN_SECRET_PATTERN_GROUPS))
    def test_every_known_group_is_accepted(self, group):
        policy = L7Policy.from_dict({"arguments": {"secretPatterns": [group]}})
        assert policy.arguments.secret_patterns == (group,)

    def test_empty_list_is_fine(self):
        policy = L7Policy.from_dict({"arguments": {"secretPatterns": []}})
        assert policy.arguments.secret_patterns == ()

    def test_absent_section_is_fine(self):
        assert L7Policy.from_dict({}).arguments.secret_patterns == ()


class TestScanTimeBehaviourIsUnchanged:
    """The residual skip stays a safety net, not a behaviour change."""

    def test_known_group_still_detects(self):
        rules = ArgumentRules(secret_patterns=("jwt",))
        payload = {"note": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW"}
        assert scan_arguments(payload, rules) != []

    def test_clean_arguments_are_clean(self):
        rules = ArgumentRules(secret_patterns=("jwt",))
        assert scan_arguments({"note": "nothing to see"}, rules) == []

    def test_unknown_group_reaching_scan_is_skipped_not_crashing(self):
        """Constructed directly, bypassing from_dict -- must not explode."""
        rules = ArgumentRules(secret_patterns=("not-a-real-group",))
        assert scan_arguments({"note": "hello"}, rules) == []
