"""A `hangar_*` tool's error payload reaches the client with `isError: true` (#1669).

A tool body that fails answers with the `ToolErrorPayload` dict (#1495). The SDK
serializes a dict as structured content with `isError: false`, so a generic MCP
client read a refusal as a success. The flag is set where every call leaves the
server, and only for Hangar's own tools and only for the exact payload shape, so
a proxied backend's answer and a real success are never relabelled.

Driven through the SDK's in-process client against a real server, so the flag the
test reads is the one a client sees, not an attribute on an intermediate object.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

from mcp_hangar._sdk_compat import new_mcp_server
from mcp_hangar.application.mcp.tooling import mcp_tool_wrapper
from mcp_hangar.fastmcp_server.tool_error_flag import install_tool_error_flag, is_error_payload

PAYLOAD = {"error": "nope", "error_type": "SomeError", "details": {}}


def _wrapped(name: str, body):
    return mcp_tool_wrapper(
        tool_name=name,
        rate_limit_key=lambda *a, **k: "global",
        check_rate_limit=lambda key: None,
    )(body)


def _server(*, install: bool = True):
    server = new_mcp_server("test")

    async def hangar_boom() -> dict[str, Any]:
        raise ValueError("it failed")

    # No return annotation: no output schema, so the payload travels only as a
    # JSON text block. This is how every `hangar_*` tool is registered.
    async def hangar_unschemaed_boom():
        raise ValueError("no schema")

    async def hangar_unschemaed_text():
        return "an error_type of nothing"

    async def hangar_fine() -> dict[str, Any]:
        return {"ok": True}

    async def hangar_odd() -> dict[str, Any]:
        # The same three keys, but not the payload: `details` is not a mapping.
        return {"error": "x", "error_type": "y", "details": "z"}

    async def hangar_plain_error() -> dict[str, Any]:
        return dict(PAYLOAD)

    async def backend_tool() -> dict[str, Any]:
        return dict(PAYLOAD)

    server.tool(name="hangar_boom")(_wrapped("hangar_boom", hangar_boom))
    server.tool(name="hangar_unschemaed_boom")(_wrapped("hangar_unschemaed_boom", hangar_unschemaed_boom))
    server.tool(name="hangar_unschemaed_text")(hangar_unschemaed_text)
    server.tool(name="hangar_fine")(hangar_fine)
    server.tool(name="hangar_odd")(hangar_odd)
    server.tool(name="hangar_plain_error")(hangar_plain_error)
    server.tool(name="backend_tool")(backend_tool)
    if install:
        install_tool_error_flag(server)
    return server


async def _call(server, name: str):
    async with Client(server) as client:
        return await client.call_tool(name, {})


class TestTheWire:
    @pytest.mark.anyio
    async def test_a_failing_tool_body_is_an_error_to_the_client(self):
        result = await _call(_server(), "hangar_boom")

        assert result.is_error is True
        assert result.structured_content["error_type"] == "ValueError"
        assert result.structured_content["error"] == "it failed"

    @pytest.mark.anyio
    async def test_a_tool_with_no_output_schema_is_flagged_from_its_text_block(self):
        result = await _call(_server(), "hangar_unschemaed_boom")

        assert result.structured_content is None
        assert result.is_error is True

    @pytest.mark.anyio
    async def test_text_that_only_mentions_error_type_is_not_the_payload(self):
        result = await _call(_server(), "hangar_unschemaed_text")

        assert result.is_error is False

    @pytest.mark.anyio
    async def test_before_the_change_it_was_a_success(self):
        """The control: the same call, without the middleware, is the bug."""
        result = await _call(_server(install=False), "hangar_boom")

        assert result.is_error is False
        assert result.structured_content["error_type"] == "ValueError"

    @pytest.mark.anyio
    async def test_a_success_is_not_relabelled(self):
        result = await _call(_server(), "hangar_fine")

        assert result.is_error is False
        assert result.structured_content == {"ok": True}

    @pytest.mark.anyio
    async def test_the_same_keys_with_other_types_are_not_the_payload(self):
        result = await _call(_server(), "hangar_odd")

        assert result.is_error is False

    @pytest.mark.anyio
    async def test_a_tool_that_is_not_hangars_is_never_touched(self):
        """A proxied backend's answer is its own, whatever keys it carries."""
        result = await _call(_server(), "backend_tool")

        assert result.is_error is False
        assert result.structured_content == PAYLOAD

    @pytest.mark.anyio
    async def test_a_hangar_tool_returning_the_exact_payload_is_an_error(self):
        result = await _call(_server(), "hangar_plain_error")

        assert result.is_error is True


class TestTheShape:
    def test_exactly_the_three_keys_with_their_types(self):
        assert is_error_payload(PAYLOAD)

    @pytest.mark.parametrize(
        "other",
        [
            None,
            "error",
            {},
            {"error": "x"},
            {**PAYLOAD, "extra": 1},
            {"error": "x", "error_type": "y"},
            {**PAYLOAD, "details": None},
            {**PAYLOAD, "error": 3},
        ],
    )
    def test_anything_else_is_not(self, other):
        assert not is_error_payload(other)


class TestInstalledOnce:
    def test_installing_twice_adds_one_middleware(self):
        from mcp_hangar._sdk_compat import lowlevel_server

        server = _server(install=False)
        install_tool_error_flag(server)
        install_tool_error_flag(server)

        assert (
            lowlevel_server(server).middleware.count(
                __import__("mcp_hangar.fastmcp_server.tool_error_flag", fromlist=["x"]).flag_tool_errors
            )
            == 1
        )
