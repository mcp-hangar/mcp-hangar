"""Unit tests for LicenseTier domain value object.

Tests the LicenseTier enum members, string conversion, and tier capability
helper methods (includes_auth, includes_behavioral, includes_full_enterprise).
"""

import pytest

from mcp_hangar.domain.value_objects.license import LicenseTier


class TestLicenseTierMembers:
    """LicenseTier enum has exactly the three expected members."""

    def test_has_community_member(self):
        assert LicenseTier.COMMUNITY.value == "community"

    def test_has_pro_member(self):
        assert LicenseTier.PRO.value == "pro"

    def test_has_enterprise_member(self):
        assert LicenseTier.ENTERPRISE.value == "enterprise"

    def test_exactly_three_members(self):
        assert len(LicenseTier) == 3


class TestLicenseTierStringConversion:
    """str(LicenseTier.X) returns the lowercase tier name."""

    def test_str_community(self):
        assert str(LicenseTier.COMMUNITY) == "community"

    def test_str_pro(self):
        assert str(LicenseTier.PRO) == "pro"

    def test_str_enterprise(self):
        assert str(LicenseTier.ENTERPRISE) == "enterprise"


class TestLicenseTierStringConstruction:
    """LicenseTier can be constructed from its string value."""

    def test_construct_from_community_string(self):
        assert LicenseTier("community") == LicenseTier.COMMUNITY

    def test_construct_from_pro_string(self):
        assert LicenseTier("pro") == LicenseTier.PRO

    def test_construct_from_enterprise_string(self):
        assert LicenseTier("enterprise") == LicenseTier.ENTERPRISE

    def test_invalid_string_raises_value_error(self):
        with pytest.raises(ValueError):
            LicenseTier("invalid_tier")


class TestIncludesAuth:
    """includes_auth() is True for PRO and ENTERPRISE, False for COMMUNITY."""

    def test_community_does_not_include_auth(self):
        assert LicenseTier.COMMUNITY.includes_auth() is False

    def test_pro_includes_auth(self):
        assert LicenseTier.PRO.includes_auth() is True

    def test_enterprise_includes_auth(self):
        assert LicenseTier.ENTERPRISE.includes_auth() is True


class TestIncludesBehavioral:
    """includes_behavioral() is True for PRO and ENTERPRISE, False for COMMUNITY."""

    def test_community_does_not_include_behavioral(self):
        assert LicenseTier.COMMUNITY.includes_behavioral() is False

    def test_pro_includes_behavioral(self):
        assert LicenseTier.PRO.includes_behavioral() is True

    def test_enterprise_includes_behavioral(self):
        assert LicenseTier.ENTERPRISE.includes_behavioral() is True


class TestIncludesFullEnterprise:
    """includes_full_enterprise() is True only for ENTERPRISE."""

    def test_community_does_not_include_full_enterprise(self):
        assert LicenseTier.COMMUNITY.includes_full_enterprise() is False

    def test_pro_does_not_include_full_enterprise(self):
        assert LicenseTier.PRO.includes_full_enterprise() is False

    def test_enterprise_includes_full_enterprise(self):
        assert LicenseTier.ENTERPRISE.includes_full_enterprise() is True
