"""`GET /api/tools` returns each tool's `input_schema` as a JSON object (#1675).

It was `str(dict)`, so a consumer got `"{'type': 'object'}"`, a Python repr with
single quotes and `True`/`None`, that no JSON parser reads.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mcp_hangar.server.api import tools as api_tools

SCHEMA = {"type": "object", "properties": {"n": {"type": "integer", "default": None}}, "required": ["n"]}


class _Tool:
    def __init__(self, schema):
        self._schema = schema

    def to_dict(self):
        out = {"name": "add", "description": "adds"}
        if self._schema is not None:
            out["inputSchema"] = self._schema
        return out


async def _list(schema):
    server = SimpleNamespace(mcp_server_id="math")

    async def dispatch(query):
        return [server] if type(query).__name__ == "ListMcpServersQuery" else [_Tool(schema)]

    with patch.object(api_tools, "dispatch_query", dispatch):
        response = await api_tools.list_all_tools(None)
    return json.loads(response.body)["tools"][0]


@pytest.mark.asyncio
async def test_the_schema_is_the_object_not_its_repr():
    tool = await _list(SCHEMA)
    assert tool["input_schema"] == SCHEMA


@pytest.mark.asyncio
async def test_a_tool_without_a_schema_reads_an_empty_object():
    tool = await _list(None)
    assert tool["input_schema"] == {}
