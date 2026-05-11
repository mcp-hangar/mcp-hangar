"""Unit tests for digest computation helper."""

import hashlib

import jcs
import pytest

from mcp_hangar.domain.services.digest_computation import compute_tool_digest
from mcp_hangar.domain.value_objects.tool_digest import ToolDigest


class TestComputeToolDigest:
    """compute_tool_digest canonical hashing behavior."""

    @pytest.fixture
    def sample_tool(self):
        return {
            "name": "read_file",
            "description": "Read a file from disk",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        }

    def test_returns_tool_digest(self, sample_tool):
        result = compute_tool_digest(sample_tool)
        assert isinstance(result, ToolDigest)
        assert result.tool_name == "read_file"
        assert result.algorithm == "sha256"
        assert len(result.sha256) == 64

    def test_deterministic(self, sample_tool):
        d1 = compute_tool_digest(sample_tool)
        d2 = compute_tool_digest(sample_tool)
        assert d1.sha256 == d2.sha256

    def test_key_order_independent(self, sample_tool):
        reordered = {
            "inputSchema": sample_tool["inputSchema"],
            "description": sample_tool["description"],
            "name": sample_tool["name"],
        }
        assert compute_tool_digest(sample_tool).sha256 == compute_tool_digest(reordered).sha256

    def test_nested_key_order_independent(self):
        tool_a = {
            "name": "t",
            "inputSchema": {"properties": {"a": {"type": "string"}, "b": {"type": "int"}}, "type": "object"},
        }
        tool_b = {
            "name": "t",
            "inputSchema": {"type": "object", "properties": {"b": {"type": "int"}, "a": {"type": "string"}}},
        }
        assert compute_tool_digest(tool_a).sha256 == compute_tool_digest(tool_b).sha256

    def test_different_description_different_digest(self, sample_tool):
        modified = {**sample_tool, "description": "DIFFERENT"}
        assert compute_tool_digest(sample_tool).sha256 != compute_tool_digest(modified).sha256

    def test_different_schema_different_digest(self, sample_tool):
        modified = {**sample_tool, "inputSchema": {"type": "string"}}
        assert compute_tool_digest(sample_tool).sha256 != compute_tool_digest(modified).sha256

    def test_different_name_different_digest(self, sample_tool):
        modified = {**sample_tool, "name": "write_file"}
        assert compute_tool_digest(sample_tool).sha256 != compute_tool_digest(modified).sha256

    def test_output_schema_included_when_present(self, sample_tool):
        with_output = {**sample_tool, "outputSchema": {"type": "string"}}
        without_output = sample_tool
        assert compute_tool_digest(with_output).sha256 != compute_tool_digest(without_output).sha256

    def test_output_schema_none_omitted(self, sample_tool):
        with_none = {**sample_tool, "outputSchema": None}
        without = sample_tool
        assert compute_tool_digest(with_none).sha256 == compute_tool_digest(without).sha256

    def test_empty_output_schema_equals_absent(self):
        tool_absent = {"name": "x"}
        tool_empty = {"name": "x", "outputSchema": {}}
        assert compute_tool_digest(tool_absent).sha256 == compute_tool_digest(tool_empty).sha256

    def test_empty_description_equals_absent(self):
        tool_absent = {"name": "x"}
        tool_empty = {"name": "x", "description": ""}
        assert compute_tool_digest(tool_absent).sha256 == compute_tool_digest(tool_empty).sha256

    def test_empty_input_schema_equals_absent(self):
        tool_absent = {"name": "x"}
        tool_empty = {"name": "x", "inputSchema": {}}
        assert compute_tool_digest(tool_absent).sha256 == compute_tool_digest(tool_empty).sha256

    def test_empty_list_input_schema_equals_absent(self):
        tool_absent = {"name": "x"}
        tool_empty = {"name": "x", "inputSchema": []}
        assert compute_tool_digest(tool_absent).sha256 == compute_tool_digest(tool_empty).sha256

    def test_populated_values_are_meaningful(self):
        base = {"name": "x"}
        with_desc = {"name": "x", "description": "d"}
        with_schema = {"name": "x", "inputSchema": {"type": "object"}}
        assert compute_tool_digest(base).sha256 != compute_tool_digest(with_desc).sha256
        assert compute_tool_digest(base).sha256 != compute_tool_digest(with_schema).sha256

    def test_extra_fields_ignored(self, sample_tool):
        with_extra = {**sample_tool, "annotations": {"readOnly": True}, "extra_field": "ignored"}
        assert compute_tool_digest(sample_tool).sha256 == compute_tool_digest(with_extra).sha256

    def test_missing_description_omitted(self):
        tool = {"name": "minimal", "inputSchema": {"type": "object"}}
        tool_with_none = {"name": "minimal", "description": None, "inputSchema": {"type": "object"}}
        result = compute_tool_digest(tool)
        assert result.tool_name == "minimal"
        assert len(result.sha256) == 64
        assert compute_tool_digest(tool).sha256 == compute_tool_digest(tool_with_none).sha256

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="'name'"):
            compute_tool_digest({"name": "", "inputSchema": {}})

    def test_missing_name_raises(self):
        with pytest.raises(ValueError, match="'name'"):
            compute_tool_digest({"description": "no name"})

    def test_none_name_raises(self):
        with pytest.raises(ValueError, match="'name'"):
            compute_tool_digest({"name": None})

    def test_numeric_name_raises(self):
        with pytest.raises(ValueError, match="'name'"):
            compute_tool_digest({"name": 123})

    def test_canonical_form_matches_jcs_reference(self):
        tool = {"name": "t", "description": "d", "inputSchema": {"type": "object"}}
        expected_payload = {"description": "d", "inputSchema": {"type": "object"}, "name": "t"}
        expected_bytes = jcs.canonicalize(expected_payload)
        expected_hash = hashlib.sha256(expected_bytes).hexdigest()
        assert compute_tool_digest(tool).sha256 == expected_hash

    def test_unicode_in_description(self):
        tool = {"name": "t", "description": "Przetwarzanie danych"}
        result = compute_tool_digest(tool)
        assert len(result.sha256) == 64

    def test_non_ascii_emoji_produces_utf8_digest(self):
        """RFC 8785 requires literal UTF-8, not \\uXXXX escapes."""
        tool = {"name": "t", "description": "cafe \u0301\U0001f680"}
        expected_payload = {"description": "cafe \u0301\U0001f680", "name": "t"}
        expected_bytes = jcs.canonicalize(expected_payload)
        expected_hash = hashlib.sha256(expected_bytes).hexdigest()
        result = compute_tool_digest(tool)
        assert result.sha256 == expected_hash

    def test_non_ascii_differs_from_escaped(self):
        """Verify JCS literal UTF-8 differs from json.dumps ensure_ascii=True."""
        import json

        tool = {"name": "t", "description": "cafe \u0301\U0001f680"}
        canonical_payload = {"description": "cafe \u0301\U0001f680", "name": "t"}
        ascii_serialized = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"))
        ascii_hash = hashlib.sha256(ascii_serialized.encode("utf-8")).hexdigest()
        jcs_hash = compute_tool_digest(tool).sha256
        assert jcs_hash != ascii_hash

    def test_empty_input_schema(self):
        tool = {"name": "t", "inputSchema": {}}
        result = compute_tool_digest(tool)
        assert len(result.sha256) == 64

    def test_empty_tool_dict_raises(self):
        with pytest.raises(ValueError, match="'name'"):
            compute_tool_digest({})
