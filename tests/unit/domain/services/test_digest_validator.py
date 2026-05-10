"""Unit tests for DigestValidator domain service."""

import pytest

from mcp_hangar.domain.events import DigestMismatchEvent
from mcp_hangar.domain.services.digest_computation import compute_tool_digest
from mcp_hangar.domain.services.digest_validator import DigestValidator
from mcp_hangar.domain.value_objects.tool_digest import (
    DigestEnforcement,
    DigestPolicy,
    DigestUnknownPolicy,
)


@pytest.fixture
def known_tool():
    return {
        "name": "read_file",
        "description": "Read a file",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
    }


@pytest.fixture
def known_digest(known_tool):
    return compute_tool_digest(known_tool)


@pytest.fixture
def block_policy(known_digest):
    return DigestPolicy(
        enforcement=DigestEnforcement.BLOCK,
        unknown=DigestUnknownPolicy.WARN,
        allowlist=frozenset([known_digest]),
    )


@pytest.fixture
def audit_policy(known_digest):
    return DigestPolicy(
        enforcement=DigestEnforcement.AUDIT,
        unknown=DigestUnknownPolicy.ALLOW_DEGRADED,
        allowlist=frozenset([known_digest]),
    )


class TestDigestValidatorMatchingDigest:
    """Behavior when observed digest matches the allowlist."""

    def test_valid_result(self, block_policy, known_tool):
        validator = DigestValidator(block_policy)
        result = validator.validate_tool(known_tool, "srv1", "corr-1")
        assert result.valid is True
        assert result.blocked is False
        assert result.event is None

    def test_valid_with_audit_policy(self, audit_policy, known_tool):
        validator = DigestValidator(audit_policy)
        result = validator.validate_tool(known_tool, "srv1", "corr-1")
        assert result.valid is True
        assert result.blocked is False
        assert result.event is None


class TestDigestValidatorMismatch:
    """Behavior when observed digest does not match the allowlist."""

    def test_block_enforcement(self, block_policy, known_tool):
        modified_tool = {**known_tool, "description": "TAMPERED"}
        validator = DigestValidator(block_policy)
        result = validator.validate_tool(modified_tool, "srv1", "corr-2")
        assert result.valid is False
        assert result.blocked is True
        assert result.event is not None
        assert result.event.enforcement == "block"
        assert result.event.tool_name == "read_file"
        assert result.event.mcp_server_id == "srv1"
        assert result.event.correlation_id == "corr-2"
        assert result.event.expected_digest is not None
        assert result.event.observed_digest is not None
        assert result.event.expected_digest != result.event.observed_digest

    def test_audit_enforcement_not_blocked(self, known_tool, known_digest):
        policy = DigestPolicy(
            enforcement=DigestEnforcement.AUDIT,
            unknown=DigestUnknownPolicy.WARN,
            allowlist=frozenset([known_digest]),
        )
        modified_tool = {**known_tool, "description": "TAMPERED"}
        validator = DigestValidator(policy)
        result = validator.validate_tool(modified_tool, "srv1", "corr-3")
        assert result.valid is False
        assert result.blocked is False
        assert result.event is not None
        assert result.event.enforcement == "audit"

    def test_warn_enforcement_not_blocked(self, known_tool, known_digest):
        policy = DigestPolicy(
            enforcement=DigestEnforcement.WARN,
            unknown=DigestUnknownPolicy.WARN,
            allowlist=frozenset([known_digest]),
        )
        modified_tool = {**known_tool, "description": "TAMPERED"}
        validator = DigestValidator(policy)
        result = validator.validate_tool(modified_tool, "srv1", "corr-4")
        assert result.valid is False
        assert result.blocked is False
        assert result.event is not None
        assert result.event.enforcement == "warn"

    def test_event_is_digest_mismatch_event(self, block_policy, known_tool):
        modified_tool = {**known_tool, "description": "X"}
        validator = DigestValidator(block_policy)
        result = validator.validate_tool(modified_tool, "srv1", "corr-5")
        assert isinstance(result.event, DigestMismatchEvent)


class TestDigestValidatorUnknownTool:
    """Behavior when tool is not in the allowlist at all."""

    @pytest.fixture
    def unknown_tool(self):
        return {"name": "new_tool", "description": "unknown", "inputSchema": {}}

    def test_unknown_warn_not_blocked(self, block_policy, unknown_tool):
        validator = DigestValidator(block_policy)
        result = validator.validate_tool(unknown_tool, "srv1", "corr-6")
        assert result.valid is False
        assert result.blocked is False
        assert result.event is not None
        assert result.event.expected_digest is None
        assert result.event.observed_digest is not None
        assert result.event.enforcement == "warn"

    def test_unknown_block_is_blocked(self, known_digest, unknown_tool):
        policy = DigestPolicy(
            enforcement=DigestEnforcement.AUDIT,
            unknown=DigestUnknownPolicy.BLOCK,
            allowlist=frozenset([known_digest]),
        )
        validator = DigestValidator(policy)
        result = validator.validate_tool(unknown_tool, "srv1", "corr-7")
        assert result.valid is False
        assert result.blocked is True
        assert result.event is not None
        assert result.event.enforcement == "block"

    def test_unknown_allow_degraded_passes(self, audit_policy, unknown_tool):
        validator = DigestValidator(audit_policy)
        result = validator.validate_tool(unknown_tool, "srv1", "corr-8")
        assert result.valid is True
        assert result.blocked is False
        assert result.event is None


class TestDigestValidatorToolList:
    """validate_tool_list batch behavior."""

    def test_mixed_results(self, block_policy, known_tool):
        modified_tool = {**known_tool, "description": "TAMPERED"}
        unknown_tool = {"name": "new", "description": "x", "inputSchema": {}}
        validator = DigestValidator(block_policy)

        results = validator.validate_tool_list([known_tool, modified_tool, unknown_tool], "srv1", "corr-9")

        assert len(results) == 3
        assert results[0].valid is True
        assert results[1].valid is False
        assert results[1].blocked is True
        assert results[2].valid is False
        assert results[2].blocked is False

    def test_empty_list(self, block_policy):
        validator = DigestValidator(block_policy)
        results = validator.validate_tool_list([], "srv1", "corr-10")
        assert results == []

    def test_all_valid(self, block_policy, known_tool):
        validator = DigestValidator(block_policy)
        results = validator.validate_tool_list([known_tool, known_tool], "srv1", "corr-11")
        assert all(r.valid for r in results)


class TestDigestValidatorEdgeCases:
    """Edge cases and boundary conditions."""

    def test_policy_property(self, block_policy):
        validator = DigestValidator(block_policy)
        assert validator.policy is block_policy

    def test_empty_allowlist_all_unknown(self):
        policy = DigestPolicy(
            enforcement=DigestEnforcement.BLOCK,
            unknown=DigestUnknownPolicy.BLOCK,
            allowlist=frozenset(),
        )
        validator = DigestValidator(policy)
        tool = {"name": "any_tool", "inputSchema": {}}
        result = validator.validate_tool(tool, "srv1", "corr-12")
        assert result.valid is False
        assert result.blocked is True

    def test_tool_with_output_schema(self, known_digest):
        tool_with_output = {
            "name": "read_file",
            "description": "Read a file",
            "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
            "outputSchema": {"type": "string"},
        }
        policy = DigestPolicy(
            enforcement=DigestEnforcement.BLOCK,
            unknown=DigestUnknownPolicy.WARN,
            allowlist=frozenset([known_digest]),
        )
        validator = DigestValidator(policy)
        result = validator.validate_tool(tool_with_output, "srv1", "corr-13")
        # Adding outputSchema changes digest -> mismatch
        assert result.valid is False
        assert result.blocked is True
