"""Tool catalog value object for providers."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSchema:
    """
    Schema for a tool provided by a provider.

    Immutable value object containing tool metadata.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        result = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        if self.output_schema is not None:
            result["outputSchema"] = self.output_schema
        return result


class ToolCatalog:
    """
    Catalog of tools provided by a provider.

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

        This is typically used when refreshing tools from a provider response.
        """
        self._tools.clear()
        for t in tool_list:
            tool = ToolSchema(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
                output_schema=t.get("outputSchema"),
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
