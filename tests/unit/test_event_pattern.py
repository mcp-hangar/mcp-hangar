"""Unit tests for EventPattern wildcard matching."""

import pytest

from mcp_hangar.domain.value_objects.event_pattern import EventPattern


class TestEventPatternConstruction:
    def test_exact_match(self):
        p = EventPattern("tools/call")
        assert p.raw == "tools/call"

    def test_wildcard_all(self):
        p = EventPattern("*")
        assert p.raw == "*"

    def test_prefix_wildcard(self):
        p = EventPattern("tools/*")
        assert p.raw == "tools/*"

    def test_suffix_wildcard(self):
        p = EventPattern("*/request")
        assert p.raw == "*/request"

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            EventPattern("")

    def test_rejects_partial_wildcard(self):
        with pytest.raises(ValueError, match="entire segment"):
            EventPattern("to*ls/call")

    def test_rejects_partial_wildcard_suffix(self):
        with pytest.raises(ValueError, match="entire segment"):
            EventPattern("tools/ca*l")

    def test_single_segment_exact_match(self):
        p = EventPattern("McpServerStarted")
        assert p.raw == "McpServerStarted"
        assert p.matches("McpServerStarted") is True
        assert p.matches("McpServerStopped") is False

    def test_rejects_empty_segment(self):
        with pytest.raises(ValueError, match="empty segment"):
            EventPattern("tools//call")

    def test_rejects_trailing_slash(self):
        with pytest.raises(ValueError, match="empty segment"):
            EventPattern("tools/")

    def test_frozen(self):
        p = EventPattern("tools/call")
        with pytest.raises(AttributeError):
            p.raw = "other"  # type: ignore[misc]

    def test_parse_alias(self):
        p = EventPattern.parse("tools/call")
        assert p.raw == "tools/call"


class TestEventPatternMatching:
    def test_exact_match_positive(self):
        assert EventPattern("tools/call").matches("tools/call") is True

    def test_exact_match_negative(self):
        assert EventPattern("tools/call").matches("tools/list") is False

    def test_star_matches_everything(self):
        assert EventPattern("*").matches("tools/call") is True
        assert EventPattern("*").matches("resources/read") is True
        assert EventPattern("*").matches("anything") is True

    def test_prefix_wildcard_matches(self):
        assert EventPattern("tools/*").matches("tools/call") is True
        assert EventPattern("tools/*").matches("tools/list") is True

    def test_prefix_wildcard_no_match_different_prefix(self):
        assert EventPattern("tools/*").matches("resources/read") is False

    def test_prefix_wildcard_no_match_extra_segments(self):
        assert EventPattern("tools/*").matches("tools/call/sub") is False

    def test_suffix_wildcard_request(self):
        assert EventPattern("*/request").matches("tools/request") is True
        assert EventPattern("*/request").matches("resources/request") is True

    def test_suffix_wildcard_response(self):
        assert EventPattern("*/response").matches("tools/response") is True

    def test_suffix_wildcard_no_match(self):
        assert EventPattern("*/request").matches("tools/call") is False

    def test_suffix_wildcard_no_match_extra_segments(self):
        assert EventPattern("*/request").matches("a/b/request") is False

    def test_three_segment_pattern(self):
        p = EventPattern("tools/*/response")
        assert p.matches("tools/call/response") is True
        assert p.matches("tools/list/response") is True
        assert p.matches("tools/call/request") is False

    def test_all_wildcards(self):
        p = EventPattern("*/*")
        assert p.matches("tools/call") is True
        assert p.matches("resources/read") is True

    def test_identity_match(self):
        names = ["tools/call", "tools/list", "resources/read", "prompts/get"]
        for name in names:
            assert EventPattern(name).matches(name) is True

    def test_str_repr(self):
        p = EventPattern("tools/*")
        assert str(p) == "tools/*"
        assert repr(p) == "EventPattern('tools/*')"
