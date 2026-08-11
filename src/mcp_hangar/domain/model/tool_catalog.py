"""Tool catalog value object for mcp_servers."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSchema:
    """
    Schema for a tool provided by a mcp_server.

    Immutable value object containing tool metadata.

    Carries the whole tool definition, not a chosen subset. It used to hold four
    fields, so `title`, `annotations`, `execution`, `icons` and `_meta` were
    dropped the moment `tools/list` came back and no projection downstream could
    put them back (#880). `annotations.readOnlyHint` / `destructiveHint` are how
    a client or an agent harness decides whether a call needs a human in front
    of it -- behind a gateway that discards them, every tool looks alike and
    that decision degrades to pattern-matching on names, which is the failure
    mode a policy enforcement plane exists to remove.

    All five are optional and default to None: an upstream that sets none of
    them produces exactly the dict this class produced before.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    #: Human-facing display name (spec `title`). What a UI shows.
    title: str | None = None
    #: Behaviour hints (spec `annotations`): readOnlyHint, destructiveHint,
    #: idempotentHint, openWorldHint. Governance inputs, not decoration.
    annotations: dict[str, Any] | None = None
    #: Spec `execution`, e.g. `{"taskSupport": "forbidden"}` -- how a client
    #: knows whether a tool must be invoked as a task.
    execution: dict[str, Any] | None = None
    #: Spec `icons`.
    icons: list[Any] | None = None
    #: The upstream's own `_meta`, carried verbatim under the wire name `_meta`.
    #: Hangar stamps its own keys into the `_meta` of RESULTS, never into a tool
    #: definition, so there is nothing here to namespace against today -- but a
    #: future stamp on this object must merge rather than replace.
    meta: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary representation, in wire (camelCase) names.

        Optional fields are omitted when unset rather than emitted as null, so
        a tool that carries none of them serialises exactly as it always did.
        """
        result: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        for key, value in (
            ("outputSchema", self.output_schema),
            ("title", self.title),
            ("annotations", self.annotations),
            ("execution", self.execution),
            ("icons", self.icons),
            ("_meta", self.meta),
        ):
            if value is not None:
                result[key] = value
        return result


class ToolCatalog:
    """
    Catalog of tools provided by a mcp_server.

    This is a mutable collection that can be updated when tools are
    discovered or refreshed. Thread safety is handled by the aggregate.
    """

    def __init__(self, tools: dict[str, ToolSchema] | None = None):
        self._tools: dict[str, ToolSchema] = dict(tools or {})

    def has(self, tool_name: str) -> bool:
        """Check if a tool exists in the catalog."""
        return tool_name in self._tools

    def get(self, tool_name: str) -> ToolSchema | None:
        """Get a tool schema by name."""
        return self._tools.get(tool_name)

    def list_names(self) -> list[str]:
        """Get list of all tool names."""
        return list(self._tools.keys())

    def list_tools(self) -> list[ToolSchema]:
        """Get list of all tool schemas."""
        return list(self._tools.values())

    def count(self) -> int:
        """Get number of tools in catalog."""
        return len(self._tools)

    def add(self, tool: ToolSchema) -> None:
        """Add or update a tool in the catalog."""
        self._tools[tool.name] = tool

    def remove(self, tool_name: str) -> bool:
        """Remove a tool from the catalog. Returns True if removed."""
        if tool_name in self._tools:
            del self._tools[tool_name]
            return True
        return False

    def clear(self) -> None:
        """Remove all tools from the catalog."""
        self._tools.clear()

    def update_from_list(self, tool_list: list[dict]) -> None:
        """
        Update catalog from a list of tool dictionaries.

        This is typically used when refreshing tools from a mcp_server response.
        """
        self._tools.clear()
        for t in tool_list:
            tool = ToolSchema(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
                output_schema=t.get("outputSchema"),
                title=t.get("title"),
                annotations=t.get("annotations"),
                execution=t.get("execution"),
                icons=t.get("icons"),
                meta=t.get("_meta"),
            )
            self._tools[tool.name] = tool

    def to_dict(self) -> dict[str, ToolSchema]:
        """Get a copy of the internal tools dictionary."""
        return dict(self._tools)

    def __contains__(self, tool_name: str) -> bool:
        return tool_name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools.values())
