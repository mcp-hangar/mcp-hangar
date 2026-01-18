"""Tests for ToolCatalog value object."""

import pytest

from mcp_hangar.domain.model.tool_catalog import ToolCatalog, ToolSchema


class TestToolSchema:
    """Test suite for ToolSchema."""

    def test_create_tool_schema(self):
        """Test creating a tool schema."""
        schema = ToolSchema(
            name="add",
            description="Add two numbers",
            input_schema={"type": "object", "properties": {"a": {"type": "number"}}},
            output_schema={"type": "number"},
        )

        assert schema.name == "add"
        assert schema.description == "Add two numbers"
        assert schema.input_schema == {
            "type": "object",
            "properties": {"a": {"type": "number"}},
        }
        assert schema.output_schema == {"type": "number"}

    def test_tool_schema_without_output_schema(self):
        """Test creating tool schema without output schema."""
        schema = ToolSchema(name="test", description="Test tool", input_schema={})

        assert schema.output_schema is None

    def test_tool_schema_to_dict(self):
        """Test converting tool schema to dictionary."""
        schema = ToolSchema(
            name="add",
            description="Add two numbers",
            input_schema={"type": "object"},
            output_schema={"type": "number"},
        )

        result = schema.to_dict()

        assert result["name"] == "add"
        assert result["description"] == "Add two numbers"
        assert result["inputSchema"] == {"type": "object"}
        assert result["outputSchema"] == {"type": "number"}

    def test_tool_schema_to_dict_without_output(self):
        """Test converting tool schema without output to dict."""
        schema = ToolSchema(name="test", description="Test tool", input_schema={})

        result = schema.to_dict()

        assert "outputSchema" not in result

    def test_tool_schema_is_immutable(self):
        """Test that tool schema is immutable (frozen dataclass)."""
        schema = ToolSchema(name="test", description="Test", input_schema={})

        with pytest.raises(AttributeError):
            schema.name = "changed"


class TestToolCatalog:
    """Test suite for ToolCatalog."""

    def test_empty_catalog(self):
        """Test empty catalog creation."""
        catalog = ToolCatalog()

        assert catalog.count() == 0
        assert len(catalog) == 0
        assert catalog.list_names() == []
        assert catalog.list_tools() == []

    def test_catalog_with_initial_tools(self):
        """Test catalog creation with initial tools."""
        schema = ToolSchema(name="add", description="Add", input_schema={})
        catalog = ToolCatalog({"add": schema})

        assert catalog.count() == 1
        assert catalog.has("add")

    def test_add_tool(self):
        """Test adding a tool to catalog."""
        catalog = ToolCatalog()
        schema = ToolSchema(name="add", description="Add", input_schema={})

        catalog.add(schema)

        assert catalog.has("add")
        assert catalog.get("add") == schema

    def test_get_existing_tool(self):
        """Test getting an existing tool."""
        schema = ToolSchema(name="add", description="Add", input_schema={})
        catalog = ToolCatalog({"add": schema})

        result = catalog.get("add")

        assert result == schema

    def test_get_nonexistent_tool(self):
        """Test getting a nonexistent tool returns None."""
        catalog = ToolCatalog()

        result = catalog.get("nonexistent")

        assert result is None

    def test_has_tool(self):
        """Test checking if tool exists."""
        schema = ToolSchema(name="add", description="Add", input_schema={})
        catalog = ToolCatalog({"add": schema})

        assert catalog.has("add") is True
        assert catalog.has("nonexistent") is False

    def test_remove_tool(self):
        """Test removing a tool from catalog."""
        schema = ToolSchema(name="add", description="Add", input_schema={})
        catalog = ToolCatalog({"add": schema})

        result = catalog.remove("add")

        assert result is True
        assert catalog.has("add") is False

    def test_remove_nonexistent_tool(self):
        """Test removing a nonexistent tool."""
        catalog = ToolCatalog()

        result = catalog.remove("nonexistent")

        assert result is False

    def test_clear(self):
        """Test clearing all tools."""
        schema1 = ToolSchema(name="add", description="Add", input_schema={})
        schema2 = ToolSchema(name="sub", description="Sub", input_schema={})
        catalog = ToolCatalog({"add": schema1, "sub": schema2})

        catalog.clear()

        assert catalog.count() == 0

    def test_list_names(self):
        """Test listing tool names."""
        schema1 = ToolSchema(name="add", description="Add", input_schema={})
        schema2 = ToolSchema(name="sub", description="Sub", input_schema={})
        catalog = ToolCatalog({"add": schema1, "sub": schema2})

        names = catalog.list_names()

        assert set(names) == {"add", "sub"}

    def test_list_tools(self):
        """Test listing tool schemas."""
        schema1 = ToolSchema(name="add", description="Add", input_schema={})
        schema2 = ToolSchema(name="sub", description="Sub", input_schema={})
        catalog = ToolCatalog({"add": schema1, "sub": schema2})

        tools = catalog.list_tools()

        assert len(tools) == 2
        assert schema1 in tools
        assert schema2 in tools

    def test_update_from_list(self):
        """Test updating catalog from list of dicts."""
        catalog = ToolCatalog()

        tool_list = [
            {
                "name": "add",
                "description": "Add two numbers",
                "inputSchema": {"type": "object"},
            },
            {
                "name": "sub",
                "description": "Subtract",
                "inputSchema": {},
                "outputSchema": {"type": "number"},
            },
        ]

        catalog.update_from_list(tool_list)

        assert catalog.count() == 2
        assert catalog.has("add")
        assert catalog.has("sub")
        assert catalog.get("add").description == "Add two numbers"
        assert catalog.get("sub").output_schema == {"type": "number"}

    def test_update_from_list_clears_existing(self):
        """Test that update_from_list clears existing tools."""
        schema = ToolSchema(name="old", description="Old", input_schema={})
        catalog = ToolCatalog({"old": schema})

        catalog.update_from_list([{"name": "new", "description": "New", "inputSchema": {}}])

        assert catalog.has("old") is False
        assert catalog.has("new") is True

    def test_to_dict(self):
        """Test converting catalog to dictionary."""
        schema = ToolSchema(name="add", description="Add", input_schema={})
        catalog = ToolCatalog({"add": schema})

        result = catalog.to_dict()

        assert isinstance(result, dict)
        assert "add" in result
        assert result["add"] == schema

    def test_contains_operator(self):
        """Test 'in' operator support."""
        schema = ToolSchema(name="add", description="Add", input_schema={})
        catalog = ToolCatalog({"add": schema})

        assert "add" in catalog
        assert "nonexistent" not in catalog

    def test_len_function(self):
        """Test len() function support."""
        schema1 = ToolSchema(name="add", description="Add", input_schema={})
        schema2 = ToolSchema(name="sub", description="Sub", input_schema={})
        catalog = ToolCatalog({"add": schema1, "sub": schema2})

        assert len(catalog) == 2

    def test_iteration(self):
        """Test iterating over catalog."""
        schema1 = ToolSchema(name="add", description="Add", input_schema={})
        schema2 = ToolSchema(name="sub", description="Sub", input_schema={})
        catalog = ToolCatalog({"add": schema1, "sub": schema2})

        tools = list(catalog)

        assert len(tools) == 2
        assert schema1 in tools
        assert schema2 in tools

    def test_add_updates_existing(self):
        """Test that adding tool with same name updates existing."""
        schema1 = ToolSchema(name="add", description="Old description", input_schema={})
        schema2 = ToolSchema(name="add", description="New description", input_schema={})
        catalog = ToolCatalog({"add": schema1})

        catalog.add(schema2)

        assert catalog.count() == 1
        assert catalog.get("add").description == "New description"
