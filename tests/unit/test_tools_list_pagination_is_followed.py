"""`tools/list` is followed to its last page (#1804).

The client read the first page only, so a tool on a later page was not listed,
not pinned and not checked for drift.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_hangar.domain.model.mcp_server import MAX_TOOLS_LIST_PAGES, _list_all_tools


def _tool(name: str) -> dict[str, Any]:
    return {"name": name, "description": "", "inputSchema": {"type": "object"}}


class Upstream:
    """Serves `pages` in order, keyed by cursor; records the cursors it was asked for."""

    def __init__(self, pages: list[list[str]]) -> None:
        self.pages = pages
        self.asked: list[str | None] = []

    def __call__(self, params: dict[str, Any]) -> dict[str, Any]:
        cursor = params.get("cursor")
        self.asked.append(cursor)
        index = int(cursor) if cursor is not None else 0
        result: dict[str, Any] = {"tools": [_tool(n) for n in self.pages[index]]}
        if index + 1 < len(self.pages):
            result["nextCursor"] = str(index + 1)
        return {"jsonrpc": "2.0", "id": 1, "result": result}


def _names(response: dict[str, Any]) -> list[str]:
    return [t["name"] for t in response["result"]["tools"]]


def test_a_single_page_is_returned_as_it_came():
    upstream = Upstream([["a", "b"]])

    response = _list_all_tools(upstream, "s")

    assert _names(response) == ["a", "b"] and upstream.asked == [None]
    assert "nextCursor" not in response["result"]


def test_every_page_is_followed_and_joined_in_order():
    upstream = Upstream([["a"], ["b"], ["c", "d"]])

    response = _list_all_tools(upstream, "s")

    assert _names(response) == ["a", "b", "c", "d"]
    assert upstream.asked == [None, "1", "2"]
    assert "nextCursor" not in response["result"]


def test_an_error_on_a_later_page_is_returned_as_that_error():
    def call(params: dict[str, Any]) -> dict[str, Any]:
        if params.get("cursor"):
            return {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "page two failed"}}
        return {"jsonrpc": "2.0", "id": 1, "result": {"tools": [_tool("a")], "nextCursor": "1"}}

    response = _list_all_tools(call, "s")

    assert response["error"]["message"] == "page two failed"


def test_an_error_on_the_first_page_is_returned_untouched():
    error = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "no"}}

    assert _list_all_tools(lambda params: error, "s") is error


def test_a_repeated_cursor_ends_the_walk_with_what_was_read():
    def call(params: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": 1, "result": {"tools": [_tool("a")], "nextCursor": "loop"}}

    response = _list_all_tools(call, "s")

    assert _names(response) == ["a", "a"]  # the first page, and the page its cursor named once


def test_the_page_limit_ends_the_walk():
    asked: list[Any] = []

    def call(params: dict[str, Any]) -> dict[str, Any]:
        asked.append(params.get("cursor"))
        return {"jsonrpc": "2.0", "id": 1, "result": {"tools": [_tool("t")], "nextCursor": str(len(asked))}}

    response = _list_all_tools(call, "s")

    assert len(asked) == MAX_TOOLS_LIST_PAGES
    assert len(response["result"]["tools"]) == MAX_TOOLS_LIST_PAGES


@pytest.mark.parametrize("cursor", [None, "", 5, ["x"]])
def test_a_missing_or_unusable_cursor_means_the_last_page(cursor):
    result: dict[str, Any] = {"tools": [_tool("a")]}
    if cursor is not None:
        result["nextCursor"] = cursor
    calls: list[dict[str, Any]] = []

    def call(params: dict[str, Any]) -> dict[str, Any]:
        calls.append(params)
        return {"jsonrpc": "2.0", "id": 1, "result": result}

    assert _names(_list_all_tools(call, "s")) == ["a"] and calls == [{}]
