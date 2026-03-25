"""Unit tests for enterprise SchemaTracker and compute_schema_hash.

Covers:
- compute_schema_hash determinism, SHA-256 properties, key order invariance
- SC45-4: First-seen check_and_store returns empty list, stores snapshot
- SC45-1: ADDED tool detection (single and multiple)
- SC45-2: REMOVED tool detection, snapshot cleanup, re-add behavior
- SC45-3: MODIFIED tool detection, description-only change immunity
- Mixed changes (ADDED + REMOVED + MODIFIED in single restart)
- Provider isolation (independent snapshots per provider)

All tests use real in-memory SQLite SchemaTracker -- no mocking.
"""

from enterprise.behavioral.schema_tracker import SchemaTracker, compute_schema_hash
from mcp_hangar.domain.model.tool_catalog import ToolSchema


def _tool(name: str, input_schema: dict | None = None) -> ToolSchema:
    """Helper to create a ToolSchema with sensible defaults."""
    return ToolSchema(
        name=name,
        description=f"Description for {name}",
        input_schema=input_schema or {"type": "object", "properties": {}},
    )


# ---------------------------------------------------------------------------
# compute_schema_hash
# ---------------------------------------------------------------------------


class TestComputeSchemaHash:
    """Tests for the compute_schema_hash function."""

    def test_deterministic(self) -> None:
        """Same name + schema produces identical hash on repeated calls."""
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        h1 = compute_schema_hash("tool_a", schema)
        h2 = compute_schema_hash("tool_a", schema)
        assert h1 == h2

    def test_different_name_different_hash(self) -> None:
        """Different tool names produce different hashes."""
        schema = {"type": "object", "properties": {}}
        h1 = compute_schema_hash("tool_a", schema)
        h2 = compute_schema_hash("tool_b", schema)
        assert h1 != h2

    def test_different_schema_different_hash(self) -> None:
        """Different input schemas produce different hashes."""
        s1 = {"type": "object", "properties": {"x": {"type": "integer"}}}
        s2 = {"type": "object", "properties": {"x": {"type": "string"}}}
        h1 = compute_schema_hash("tool_a", s1)
        h2 = compute_schema_hash("tool_a", s2)
        assert h1 != h2

    def test_key_order_irrelevant(self) -> None:
        """JSON key order does not affect hash (sort_keys=True internally)."""
        s1 = {"b": 1, "a": 2}
        s2 = {"a": 2, "b": 1}
        h1 = compute_schema_hash("tool_a", s1)
        h2 = compute_schema_hash("tool_a", s2)
        assert h1 == h2

    def test_sha256_length(self) -> None:
        """Hash output is a 64-character hex string (SHA-256)."""
        h = compute_schema_hash("tool_a", {"type": "object"})
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# SC45-4: First-seen
# ---------------------------------------------------------------------------


class TestFirstSeen:
    """SC45-4: First-seen schemas stored without alerting."""

    def test_first_seen_returns_empty(self) -> None:
        """First check_and_store for a provider returns no changes."""
        tracker = SchemaTracker(":memory:")
        tools = [_tool("add"), _tool("subtract")]
        changes = tracker.check_and_store("math", tools)
        assert changes == []

    def test_first_seen_stores_snapshot(self) -> None:
        """After first call, snapshot contains all tool names."""
        tracker = SchemaTracker(":memory:")
        tools = [_tool("add"), _tool("subtract")]
        tracker.check_and_store("math", tools)

        snapshot = tracker.get_snapshot("math")
        tool_names = sorted(row["tool_name"] for row in snapshot)
        assert tool_names == ["add", "subtract"]


# ---------------------------------------------------------------------------
# SC45-1: ADDED
# ---------------------------------------------------------------------------


class TestAdded:
    """SC45-1: Provider restarting with a new tool emits ADDED."""

    def test_added_tool_detected(self) -> None:
        """New tool on second startup returns change_type=added, old_hash=None."""
        tracker = SchemaTracker(":memory:")
        # First startup: baseline
        tracker.check_and_store("math", [_tool("add")])
        # Second startup: add new tool
        changes = tracker.check_and_store("math", [_tool("add"), _tool("multiply")])

        assert len(changes) == 1
        change = changes[0]
        assert change["tool_name"] == "multiply"
        assert change["change_type"] == "added"
        assert change["old_hash"] is None
        assert change["new_hash"] is not None

    def test_multiple_added_tools(self) -> None:
        """Multiple new tools each detected as ADDED."""
        tracker = SchemaTracker(":memory:")
        tracker.check_and_store("math", [_tool("add")])
        changes = tracker.check_and_store(
            "math",
            [_tool("add"), _tool("multiply"), _tool("divide")],
        )

        added_names = sorted(c["tool_name"] for c in changes if c["change_type"] == "added")
        assert added_names == ["divide", "multiply"]


# ---------------------------------------------------------------------------
# SC45-2: REMOVED
# ---------------------------------------------------------------------------


class TestRemoved:
    """SC45-2: Provider restarting with a removed tool emits REMOVED."""

    def test_removed_tool_detected(self) -> None:
        """Missing tool on second startup returns change_type=removed, new_hash=None."""
        tracker = SchemaTracker(":memory:")
        tracker.check_and_store("math", [_tool("add"), _tool("subtract")])
        changes = tracker.check_and_store("math", [_tool("add")])

        assert len(changes) == 1
        change = changes[0]
        assert change["tool_name"] == "subtract"
        assert change["change_type"] == "removed"
        assert change["old_hash"] is not None
        assert change["new_hash"] is None

    def test_removed_tool_cleaned_from_snapshot(self) -> None:
        """After removal, snapshot no longer contains the removed tool."""
        tracker = SchemaTracker(":memory:")
        tracker.check_and_store("math", [_tool("add"), _tool("subtract")])
        tracker.check_and_store("math", [_tool("add")])

        snapshot = tracker.get_snapshot("math")
        tool_names = [row["tool_name"] for row in snapshot]
        assert "subtract" not in tool_names
        assert "add" in tool_names

    def test_all_tools_removed(self) -> None:
        """All tools removed produces REMOVED for each."""
        tracker = SchemaTracker(":memory:")
        tracker.check_and_store("math", [_tool("add"), _tool("subtract")])
        changes = tracker.check_and_store("math", [])

        assert len(changes) == 2
        removed_names = sorted(c["tool_name"] for c in changes)
        assert removed_names == ["add", "subtract"]
        assert all(c["change_type"] == "removed" for c in changes)

    def test_removed_then_re_added_is_added(self) -> None:
        """Re-adding a previously removed tool shows as ADDED."""
        tracker = SchemaTracker(":memory:")
        tracker.check_and_store("math", [_tool("add"), _tool("subtract")])
        # Remove subtract
        tracker.check_and_store("math", [_tool("add")])
        # Re-add subtract
        changes = tracker.check_and_store("math", [_tool("add"), _tool("subtract")])

        assert len(changes) == 1
        change = changes[0]
        assert change["tool_name"] == "subtract"
        assert change["change_type"] == "added"


# ---------------------------------------------------------------------------
# SC45-3: MODIFIED
# ---------------------------------------------------------------------------


class TestModified:
    """SC45-3: Provider restarting with modified parameters emits MODIFIED."""

    def test_modified_tool_detected(self) -> None:
        """Changed input_schema returns change_type=modified with different hashes."""
        tracker = SchemaTracker(":memory:")
        schema_v1 = {"type": "object", "properties": {"x": {"type": "integer"}}}
        schema_v2 = {"type": "object", "properties": {"x": {"type": "string"}, "y": {"type": "integer"}}}
        tracker.check_and_store("math", [_tool("add", schema_v1)])
        changes = tracker.check_and_store("math", [_tool("add", schema_v2)])

        assert len(changes) == 1
        change = changes[0]
        assert change["tool_name"] == "add"
        assert change["change_type"] == "modified"
        assert change["old_hash"] is not None
        assert change["new_hash"] is not None
        assert change["old_hash"] != change["new_hash"]

    def test_description_change_not_detected(self) -> None:
        """Only description changed -> no changes (hash ignores description)."""
        tracker = SchemaTracker(":memory:")
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        tool_v1 = ToolSchema(name="add", description="Adds numbers", input_schema=schema)
        tool_v2 = ToolSchema(name="add", description="Sum two values together", input_schema=schema)
        tracker.check_and_store("math", [tool_v1])
        changes = tracker.check_and_store("math", [tool_v2])

        assert changes == []


# ---------------------------------------------------------------------------
# Mixed changes
# ---------------------------------------------------------------------------


class TestMixedChanges:
    """Mixed ADDED + REMOVED + MODIFIED in a single restart."""

    def test_added_removed_modified_together(self) -> None:
        """Single restart with all three change types detected correctly."""
        tracker = SchemaTracker(":memory:")
        schema_v1 = {"type": "object", "properties": {"x": {"type": "integer"}}}
        schema_v2 = {"type": "object", "properties": {"x": {"type": "string"}}}

        # Baseline: add, subtract, multiply
        tracker.check_and_store(
            "math",
            [
                _tool("add", schema_v1),
                _tool("subtract"),
                _tool("multiply"),
            ],
        )

        # Second startup: add is modified, subtract is removed, divide is added
        changes = tracker.check_and_store(
            "math",
            [
                _tool("add", schema_v2),
                _tool("multiply"),
                _tool("divide"),
            ],
        )

        change_map = {c["tool_name"]: c["change_type"] for c in changes}
        assert change_map == {
            "add": "modified",
            "subtract": "removed",
            "divide": "added",
        }


# ---------------------------------------------------------------------------
# Provider isolation
# ---------------------------------------------------------------------------


class TestProviderIsolation:
    """Different providers have independent snapshots."""

    def test_different_providers_independent(self) -> None:
        """Changes to one provider's tools do not affect another."""
        tracker = SchemaTracker(":memory:")

        # Provider A: add, subtract
        tracker.check_and_store("provider-a", [_tool("add"), _tool("subtract")])
        # Provider B: multiply
        tracker.check_and_store("provider-b", [_tool("multiply")])

        # Provider A loses subtract -> REMOVED for A
        changes_a = tracker.check_and_store("provider-a", [_tool("add")])
        # Provider B adds divide -> ADDED for B
        changes_b = tracker.check_and_store("provider-b", [_tool("multiply"), _tool("divide")])

        assert len(changes_a) == 1
        assert changes_a[0]["tool_name"] == "subtract"
        assert changes_a[0]["change_type"] == "removed"

        assert len(changes_b) == 1
        assert changes_b[0]["tool_name"] == "divide"
        assert changes_b[0]["change_type"] == "added"
