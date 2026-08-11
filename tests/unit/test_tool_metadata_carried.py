"""A tool definition survives the gateway intact (#880).

Both projections used to keep `name` / `description` / `inputSchema` and drop
everything else, so `annotations.readOnlyHint` and `destructiveHint` -- how a
client or an agent harness decides whether a call needs a human in front of it
-- never reached anyone. Behind Hangar every tool looked alike.

The loss was at DISCOVERY, not in the projections: `ToolCatalog.update_from_list`
built a four-field value object, so there was nothing downstream to carry. These
cover the whole path, plus the two invariants a patch release must not disturb:
digests (and therefore pins) and the shape of a tool that sets none of this.
"""

from __future__ import annotations

from typing import Any

# Import order matters here, for a reason this test does not own. Importing
# `fastmcp_server.flat_tool_projection` FIRST trips a pre-existing import cycle:
# it imports `server.tools.batch`, whose package chain reaches `server.bootstrap`,
# which imports `flat_tool_projection` back while it is still initialising. The
# full suite hides it because something else always imports `mcp_hangar.server`
# first; running this file alone does not. Present on `main` before this change
# -- verified by importing the module on a clean tree -- and filed separately.
import mcp_hangar.server  # noqa: F401  -- see above

from mcp_hangar.application.read_models.mcp_server_views import ToolInfo
from mcp_hangar.domain.model.tool_catalog import ToolCatalog, ToolSchema
from mcp_hangar.domain.services.digest_computation import compute_tool_digest

# The reference server's `get-annotated-message`, as it appears on the wire.
ANNOTATED_TOOL: dict[str, Any] = {
    "name": "get-annotated-message",
    "title": "Get Annotated Message Tool",
    "description": "Demonstrates how annotations work",
    "inputSchema": {"type": "object", "properties": {}},
    "outputSchema": {"type": "object"},
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "execution": {"taskSupport": "forbidden"},
    "icons": [{"src": "https://example.invalid/i.png"}],
    "_meta": {"io.example/owner": "payments"},
}

PLAIN_TOOL: dict[str, Any] = {
    "name": "add",
    "description": "Add two numbers",
    "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}}},
}


class TestDiscovery:
    def test_the_whole_definition_survives_tools_list(self) -> None:
        catalog = ToolCatalog()

        catalog.update_from_list([ANNOTATED_TOOL])

        tool = catalog.get("get-annotated-message")
        assert tool is not None
        assert tool.title == "Get Annotated Message Tool"
        assert tool.annotations == ANNOTATED_TOOL["annotations"]
        assert tool.execution == {"taskSupport": "forbidden"}
        assert tool.icons == ANNOTATED_TOOL["icons"]
        assert tool.meta == {"io.example/owner": "payments"}

    def test_it_round_trips_back_to_the_wire(self) -> None:
        catalog = ToolCatalog()
        catalog.update_from_list([ANNOTATED_TOOL])

        assert catalog.get("get-annotated-message").to_dict() == ANNOTATED_TOOL  # type: ignore[union-attr]

    def test_a_tool_that_sets_none_of_it_is_unchanged(self) -> None:
        """No nulls invented: the old four-field output, byte for byte."""
        catalog = ToolCatalog()
        catalog.update_from_list([PLAIN_TOOL])

        assert catalog.get("add").to_dict() == PLAIN_TOOL  # type: ignore[union-attr]


class TestDigestsAreUndisturbed:
    """Pins must not move. A patch that silently invalidated every pinned digest
    would fail closed at invocation on `digest_enforcement: block`."""

    def test_annotations_do_not_change_the_digest(self) -> None:
        bare = {k: v for k, v in ANNOTATED_TOOL.items() if k in ("name", "description", "inputSchema", "outputSchema")}

        assert compute_tool_digest(ANNOTATED_TOOL) == compute_tool_digest(bare)

    def test_the_pinned_surface_still_moves_when_it_should(self) -> None:
        changed = {**ANNOTATED_TOOL, "description": "something else"}

        assert compute_tool_digest(changed) != compute_tool_digest(ANNOTATED_TOOL)


class TestFlatProjection:
    def test_the_front_door_carries_the_metadata(self) -> None:
        from mcp_hangar._sdk_compat import Tool as MCPTool
        from mcp_hangar.fastmcp_server.flat_tool_projection import _build_mcp_tool_list
        from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry

        registry = get_tool_projection_registry()
        registry.build_from_tools(
            "everything",
            [
                ToolSchema(
                    name="get-annotated-message",
                    description="Demonstrates how annotations work",
                    input_schema=ANNOTATED_TOOL["inputSchema"],
                    output_schema=ANNOTATED_TOOL["outputSchema"],
                    title=ANNOTATED_TOOL["title"],
                    annotations=ANNOTATED_TOOL["annotations"],
                    execution=ANNOTATED_TOOL["execution"],
                    icons=ANNOTATED_TOOL["icons"],
                    meta=ANNOTATED_TOOL["_meta"],
                )
            ],
        )
        try:
            tools = _build_mcp_tool_list({"get-annotated-message": ("everything", "get-annotated-message")})
        finally:
            # Process-global singleton: leave it as found or the next test in the
            # session inherits this fixture.
            registry.invalidate()

        assert len(tools) == 1
        served = tools[0].model_dump(mode="json", by_alias=True, exclude_none=True)
        assert isinstance(tools[0], MCPTool)
        assert served["title"] == "Get Annotated Message Tool"
        assert served["annotations"]["readOnlyHint"] is True
        assert served["annotations"]["destructiveHint"] is False
        assert served["execution"] == {"taskSupport": "forbidden"}
        # Dropped by the old three-key build, and the reason a client behind the
        # front door had nothing to validate structured output against.
        assert served["outputSchema"] == {"type": "object"}
        assert served["_meta"] == {"io.example/owner": "payments"}


class TestRestView:
    def test_the_inspection_surface_agrees_with_what_is_served(self) -> None:
        tool = ToolSchema(
            name="t",
            description="d",
            input_schema={},
            annotations={"destructiveHint": True},
            title="T",
        )

        info = ToolInfo(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            output_schema=tool.output_schema,
            title=tool.title,
            annotations=tool.annotations,
            execution=tool.execution,
            icons=tool.icons,
            meta=tool.meta,
        )

        assert info.to_dict() == tool.to_dict()
