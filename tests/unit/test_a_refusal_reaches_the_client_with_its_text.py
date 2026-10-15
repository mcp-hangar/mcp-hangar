"""A refusal a caller is meant to read keeps its text on the new SDK.

mcp 2.3.0 reports any exception a tool raises, other than its own `ToolError`,
as `Error executing tool <name>` and nothing more. The tool wrappers raise a
`PermissionError` for "not authorized" and "session suspended" and a
`ValueError` for an argument that is not valid, so a client was refused and not
told why: six live tests failed on it. Those are raised as `ToolError` now.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

from mcp_hangar._sdk_compat import FastMCP, InvalidArgument, SdkToolError
from mcp_hangar.application.mcp.tooling import get_tool_authorizer, key_global, mcp_tool_wrapper, set_tool_authorizer
from mcp_hangar.server.session_guard import SESSION_SUSPENDED_MESSAGE, SessionSuspendedError
from mcp_hangar.server.tools.tool_permissions import ToolAccessNotAuthorizedError


@pytest.fixture(autouse=True)
def _authorizer_restored() -> Iterator[None]:
    before = get_tool_authorizer()
    yield
    set_tool_authorizer(before)


def _wrapped(validate: Any = None):
    @mcp_tool_wrapper(
        tool_name="probe", rate_limit_key=key_global, check_rate_limit=lambda key: None, validate=validate
    )
    def probe(name: str = "x") -> dict:
        return {"ok": name}

    return probe


def test_an_authorization_refusal_is_a_tool_error_with_its_text():
    def refuse(tool_name: str, mcp_ctx: Any) -> None:
        raise ToolAccessNotAuthorizedError("Not authorized to call 'probe': mcp_servers:lifecycle permission required")

    set_tool_authorizer(refuse)

    with pytest.raises(SdkToolError, match="Not authorized to call 'probe': mcp_servers:lifecycle"):
        _wrapped()()


def test_a_suspended_session_is_a_tool_error_with_its_text():
    def refuse(tool_name: str, mcp_ctx: Any) -> None:
        raise SessionSuspendedError()

    set_tool_authorizer(refuse)

    with pytest.raises(SdkToolError, match=SESSION_SUSPENDED_MESSAGE):
        _wrapped()()


def test_the_refusals_are_still_the_exceptions_callers_catch():
    assert issubclass(ToolAccessNotAuthorizedError, PermissionError)
    assert issubclass(SessionSuspendedError, PermissionError)
    assert issubclass(InvalidArgument, ValueError)


def test_an_invalid_argument_is_a_tool_error_with_its_text():
    def invalid(name: str = "x") -> None:
        raise ValueError("mcp_server id must not be empty")

    set_tool_authorizer(None)

    with pytest.raises(SdkToolError, match="mcp_server id must not be empty"):
        _wrapped(validate=invalid)()


def test_a_permitted_call_is_untouched():
    set_tool_authorizer(None)

    assert _wrapped()("y") == {"ok": "y"}


def test_through_the_sdk_the_client_is_told_why():
    """The SDK itself, not the wrapper alone: the text has to survive its own handling."""

    def refuse(tool_name: str, mcp_ctx: Any) -> None:
        raise ToolAccessNotAuthorizedError("Not authorized to call 'probe'")

    set_tool_authorizer(refuse)
    server = FastMCP("probe-server")
    server.tool(name="probe")(_wrapped())

    with pytest.raises(Exception, match="Not authorized to call 'probe'") as caught:
        asyncio.run(server.call_tool("probe", {}))
    assert "Error executing tool probe" in str(caught.value)
