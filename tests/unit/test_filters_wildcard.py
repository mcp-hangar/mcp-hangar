"""Unit tests for wildcard support in ws/filters.py."""

from mcp_hangar.domain.events import DomainEvent
from mcp_hangar.server.api.ws.filters import compile_event_patterns, matches_filters


class _FakeEvent(DomainEvent):
    def __init__(self, event_type: str, mcp_server_id: str = "srv-1") -> None:
        super().__init__()
        self._event_type = event_type
        self._mcp_server_id = mcp_server_id

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["event_type"] = self._event_type
        d["mcp_server_id"] = self._mcp_server_id
        return d


class TestCompileEventPatterns:

    def test_exact_patterns(self):
        patterns = compile_event_patterns(["tools/call", "tools/list"])
        assert len(patterns) == 2

    def test_wildcard_patterns(self):
        patterns = compile_event_patterns(["tools/*", "*/request"])
        assert len(patterns) == 2

    def test_invalid_pattern_dropped(self):
        patterns = compile_event_patterns(["tools/call", "to*ls/bad", "tools/list"])
        assert len(patterns) == 2

    def test_star_pattern(self):
        patterns = compile_event_patterns(["*"])
        assert len(patterns) == 1


class TestMatchesFiltersWildcard:

    def test_exact_match_still_works(self):
        filters = {"event_types": ["McpServerStarted"]}
        assert matches_filters(_FakeEvent("McpServerStarted"), filters) is True
        assert matches_filters(_FakeEvent("McpServerStopped"), filters) is False

    def test_wildcard_star_matches_all(self):
        filters = {"event_types": ["*"]}
        assert matches_filters(_FakeEvent("McpServerStarted"), filters) is True
        assert matches_filters(_FakeEvent("tools/call"), filters) is True

    def test_prefix_wildcard(self):
        filters = {"event_types": ["tools/*"]}
        assert matches_filters(_FakeEvent("tools/call"), filters) is True
        assert matches_filters(_FakeEvent("tools/list"), filters) is True
        assert matches_filters(_FakeEvent("resources/read"), filters) is False

    def test_suffix_wildcard(self):
        filters = {"event_types": ["*/request"]}
        assert matches_filters(_FakeEvent("tools/request"), filters) is True
        assert matches_filters(_FakeEvent("resources/request"), filters) is True
        assert matches_filters(_FakeEvent("tools/call"), filters) is False

    def test_mixed_patterns(self):
        filters = {"event_types": ["tools/*", "McpServerStarted"]}
        assert matches_filters(_FakeEvent("tools/call"), filters) is True
        assert matches_filters(_FakeEvent("McpServerStarted"), filters) is True

    def test_empty_filters_passes_all(self):
        assert matches_filters(_FakeEvent("anything"), {}) is True

    def test_mcp_server_ids_still_works(self):
        filters = {"mcp_server_ids": ["srv-1"]}
        assert matches_filters(_FakeEvent("x/y", mcp_server_id="srv-1"), filters) is True
        assert matches_filters(_FakeEvent("x/y", mcp_server_id="srv-2"), filters) is False

    def test_combined_event_types_and_mcp_server_ids(self):
        filters = {"event_types": ["tools/*"], "mcp_server_ids": ["srv-1"]}
        assert matches_filters(_FakeEvent("tools/call", "srv-1"), filters) is True
        assert matches_filters(_FakeEvent("tools/call", "srv-2"), filters) is False
        assert matches_filters(_FakeEvent("resources/read", "srv-1"), filters) is False
