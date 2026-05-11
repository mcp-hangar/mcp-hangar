"""Unit tests for ToolDigest and DigestPolicy value objects."""

import pytest

from mcp_hangar.domain.value_objects.tool_digest import (
    DigestEnforcement,
    DigestPolicy,
    DigestUnknownPolicy,
    ToolDigest,
    normalize_unknown_policy,
)


class TestToolDigest:
    """ToolDigest value object validation and behavior."""

    def test_valid_digest(self):
        td = ToolDigest(tool_name="read_file", sha256="a" * 64)
        assert td.tool_name == "read_file"
        assert td.sha256 == "a" * 64
        assert td.algorithm == "sha256"

    def test_custom_algorithm(self):
        td = ToolDigest(tool_name="x", sha256="b" * 64, algorithm="sha256-v2")
        assert td.algorithm == "sha256-v2"

    def test_frozen_immutability(self):
        td = ToolDigest(tool_name="x", sha256="c" * 64)
        with pytest.raises(AttributeError):
            td.tool_name = "y"  # type: ignore[misc]

    def test_rejects_empty_tool_name(self):
        with pytest.raises(ValueError, match="tool_name cannot be empty"):
            ToolDigest(tool_name="", sha256="a" * 64)

    def test_rejects_short_hash(self):
        with pytest.raises(ValueError, match="64 lowercase hex"):
            ToolDigest(tool_name="x", sha256="abc")

    def test_rejects_uppercase_hash(self):
        with pytest.raises(ValueError, match="64 lowercase hex"):
            ToolDigest(tool_name="x", sha256="A" * 64)

    def test_rejects_non_hex_characters(self):
        with pytest.raises(ValueError, match="64 lowercase hex"):
            ToolDigest(tool_name="x", sha256="g" * 64)

    def test_rejects_65_char_hash(self):
        with pytest.raises(ValueError, match="64 lowercase hex"):
            ToolDigest(tool_name="x", sha256="a" * 65)

    def test_rejects_63_char_hash(self):
        with pytest.raises(ValueError, match="64 lowercase hex"):
            ToolDigest(tool_name="x", sha256="a" * 63)

    def test_rejects_empty_algorithm(self):
        with pytest.raises(ValueError, match="algorithm cannot be empty"):
            ToolDigest(tool_name="x", sha256="a" * 64, algorithm="")

    def test_equality(self):
        td1 = ToolDigest(tool_name="x", sha256="a" * 64)
        td2 = ToolDigest(tool_name="x", sha256="a" * 64)
        assert td1 == td2

    def test_inequality_different_hash(self):
        td1 = ToolDigest(tool_name="x", sha256="a" * 64)
        td2 = ToolDigest(tool_name="x", sha256="b" * 64)
        assert td1 != td2

    def test_inequality_different_name(self):
        td1 = ToolDigest(tool_name="x", sha256="a" * 64)
        td2 = ToolDigest(tool_name="y", sha256="a" * 64)
        assert td1 != td2

    def test_hashable_for_frozenset(self):
        td1 = ToolDigest(tool_name="x", sha256="a" * 64)
        td2 = ToolDigest(tool_name="y", sha256="b" * 64)
        fs = frozenset([td1, td2])
        assert len(fs) == 2
        assert td1 in fs

    def test_accepts_all_hex_digits(self):
        valid_hex = "0123456789abcdef" * 4
        td = ToolDigest(tool_name="t", sha256=valid_hex)
        assert td.sha256 == valid_hex


class TestDigestEnforcement:
    """DigestEnforcement enum values."""

    def test_values(self):
        assert DigestEnforcement.AUDIT == "audit"
        assert DigestEnforcement.WARN == "warn"
        assert DigestEnforcement.BLOCK == "block"

    def test_from_string(self):
        assert DigestEnforcement("audit") == DigestEnforcement.AUDIT
        assert DigestEnforcement("block") == DigestEnforcement.BLOCK

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            DigestEnforcement("invalid")


class TestDigestUnknownPolicy:
    """DigestUnknownPolicy enum values."""

    def test_values(self):
        assert DigestUnknownPolicy.ALLOW_UNVERIFIED == "allow_unverified"
        assert DigestUnknownPolicy.WARN == "warn"
        assert DigestUnknownPolicy.BLOCK == "block"

    def test_from_string(self):
        assert DigestUnknownPolicy("allow_unverified") == DigestUnknownPolicy.ALLOW_UNVERIFIED


class TestNormalizeUnknownPolicy:
    """Deprecation shim for allow_degraded -> allow_unverified."""

    def test_deprecated_alias_returns_new_value(self):
        with pytest.warns(DeprecationWarning, match="allow_degraded.*deprecated.*allow_unverified"):
            result = normalize_unknown_policy("allow_degraded")
        assert result == "allow_unverified"

    def test_deprecated_alias_mentions_removal_version(self):
        with pytest.warns(DeprecationWarning, match="v1\\.4"):
            normalize_unknown_policy("allow_degraded")

    def test_current_values_pass_through(self):
        assert normalize_unknown_policy("allow_unverified") == "allow_unverified"
        assert normalize_unknown_policy("warn") == "warn"
        assert normalize_unknown_policy("block") == "block"

    def test_unknown_value_passes_through(self):
        assert normalize_unknown_policy("something_else") == "something_else"


class TestDigestPolicy:
    """DigestPolicy value object behavior."""

    @pytest.fixture
    def sample_digest(self):
        return ToolDigest(tool_name="read_file", sha256="a" * 64)

    @pytest.fixture
    def sample_policy(self, sample_digest):
        return DigestPolicy(
            enforcement=DigestEnforcement.BLOCK,
            unknown=DigestUnknownPolicy.WARN,
            allowlist=frozenset([sample_digest]),
        )

    def test_construction(self, sample_policy, sample_digest):
        assert sample_policy.enforcement == DigestEnforcement.BLOCK
        assert sample_policy.unknown == DigestUnknownPolicy.WARN
        assert sample_digest in sample_policy.allowlist

    def test_frozen_immutability(self, sample_policy):
        with pytest.raises(AttributeError):
            sample_policy.enforcement = DigestEnforcement.AUDIT  # type: ignore[misc]

    def test_get_expected_digest_found(self, sample_policy, sample_digest):
        result = sample_policy.get_expected_digest("read_file")
        assert result == sample_digest

    def test_get_expected_digest_not_found(self, sample_policy):
        result = sample_policy.get_expected_digest("nonexistent_tool")
        assert result is None

    def test_empty_allowlist(self):
        policy = DigestPolicy(
            enforcement=DigestEnforcement.AUDIT,
            unknown=DigestUnknownPolicy.ALLOW_UNVERIFIED,
            allowlist=frozenset(),
        )
        assert policy.get_expected_digest("anything") is None

    def test_multiple_tools_in_allowlist(self):
        d1 = ToolDigest(tool_name="tool_a", sha256="a" * 64)
        d2 = ToolDigest(tool_name="tool_b", sha256="b" * 64)
        policy = DigestPolicy(
            enforcement=DigestEnforcement.WARN,
            unknown=DigestUnknownPolicy.BLOCK,
            allowlist=frozenset([d1, d2]),
        )
        assert policy.get_expected_digest("tool_a") == d1
        assert policy.get_expected_digest("tool_b") == d2
        assert policy.get_expected_digest("tool_c") is None

    def test_rejects_invalid_enforcement_type(self):
        with pytest.raises(TypeError, match="enforcement must be"):
            DigestPolicy(
                enforcement="block",  # type: ignore[arg-type]
                unknown=DigestUnknownPolicy.WARN,
                allowlist=frozenset(),
            )

    def test_rejects_invalid_unknown_type(self):
        with pytest.raises(TypeError, match="unknown must be"):
            DigestPolicy(
                enforcement=DigestEnforcement.BLOCK,
                unknown="warn",  # type: ignore[arg-type]
                allowlist=frozenset(),
            )

    def test_rejects_non_frozenset_allowlist(self):
        with pytest.raises(TypeError, match="allowlist must be a frozenset"):
            DigestPolicy(
                enforcement=DigestEnforcement.BLOCK,
                unknown=DigestUnknownPolicy.WARN,
                allowlist=set(),  # type: ignore[arg-type]
            )
