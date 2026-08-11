"""Advertised capabilities must be ones this server actually serves (#888).

The handshake claimed `prompts` and `resources` on every deployment and served
neither, because the SDK derives both from handlers FastMCP registers
unconditionally. These assert the claim now follows the content -- in both
directions, so the day an upstream prompt/resource proxy lands (#889) the
capability comes back on its own rather than being re-hard-coded.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_hangar._sdk_compat import FastMCP, lowlevel_server
from mcp_hangar.fastmcp_server.served_capabilities import (
    PROMPT_METHODS,
    RESOURCE_METHODS,
    withdraw_unserved_capabilities,
)


def _capabilities(mcp: FastMCP, protocol_version: str | None = None) -> dict[str, Any]:
    kwargs = {"protocol_version": protocol_version} if protocol_version else {}
    caps = lowlevel_server(mcp).get_capabilities(**kwargs)
    return caps.model_dump(mode="json", by_alias=True, exclude_none=True)


def test_a_bare_server_advertises_prompts_and_resources_before_the_fix() -> None:
    """The defect, pinned: this is what the SDK gives us out of the box."""
    advertised = _capabilities(FastMCP("t"))

    assert "prompts" in advertised
    assert "resources" in advertised


def test_withdrawn_when_nothing_is_registered() -> None:
    mcp = FastMCP("t")
    low = lowlevel_server(mcp)
    registered_before = {m for m in PROMPT_METHODS + RESOURCE_METHODS if low.get_request_handler(m) is not None}
    assert registered_before, "SDK registered no prompt/resource handlers -- this test proves nothing"

    withdrawn = withdraw_unserved_capabilities(mcp)

    advertised = _capabilities(mcp)
    assert "prompts" not in advertised
    assert "resources" not in advertised
    # Exactly what was there, nothing invented: the SDK does not register the
    # two subscription methods, so a hardcoded expectation would be wrong.
    assert set(withdrawn) == registered_before
    # tools survive: the one capability that IS served must be untouched.
    assert "tools" in advertised


@pytest.mark.parametrize("protocol_version", [None, "2026-07-28"])
def test_withdrawn_in_both_protocol_eras(protocol_version: str | None) -> None:
    """`get_capabilities` derives the flags differently per era; the claim must go in both."""
    mcp = FastMCP("t")

    withdraw_unserved_capabilities(mcp)

    advertised = _capabilities(mcp, protocol_version)
    assert "prompts" not in advertised
    assert "resources" not in advertised


def test_the_methods_stop_being_dispatchable() -> None:
    """Method-not-found is the honest answer once the capability is not claimed.

    An empty list told a conformant client *this server has no prompts*, which
    is a different statement from *this gateway does not carry prompts* -- and
    the client had no way to tell them apart.
    """
    mcp = FastMCP("t")
    low = lowlevel_server(mcp)

    withdraw_unserved_capabilities(mcp)

    for method in PROMPT_METHODS + RESOURCE_METHODS:
        assert low.get_request_handler(method) is None, method


def test_a_registered_prompt_keeps_the_capability() -> None:
    """Derived, not inverted: #889 registers handlers and this must get out of the way."""
    mcp = FastMCP("t")

    @mcp.prompt(name="greet")
    def _greet() -> str:
        return "hello"

    withdrawn = withdraw_unserved_capabilities(mcp)

    advertised = _capabilities(mcp)
    assert "prompts" in advertised
    assert "resources" not in advertised
    assert not set(withdrawn) & set(PROMPT_METHODS)


def test_a_registered_resource_keeps_the_capability() -> None:
    mcp = FastMCP("t")

    @mcp.resource("demo://thing")
    def _thing() -> str:
        return "content"

    withdraw_unserved_capabilities(mcp)

    advertised = _capabilities(mcp)
    assert "resources" in advertised
    assert "prompts" not in advertised


def test_an_undeterminable_surface_is_left_advertised(caplog: pytest.LogCaptureFixture) -> None:
    """Fail towards the status quo: withdrawing a served capability is the worse error."""
    mcp = FastMCP("t")
    del mcp._prompt_manager

    withdrawn = withdraw_unserved_capabilities(mcp)

    assert "prompts" in _capabilities(mcp)
    assert not set(withdrawn) & set(PROMPT_METHODS)
    assert any("served_capabilities_undeterminable" in r.message for r in caplog.records)


def test_every_advertised_capability_is_one_hangar_populates() -> None:
    """The conformance assertion #888 asks for, over the SHIPPED server surface.

    Note what this does NOT assert: "the capability has a handler". A handler is
    always there -- that is the whole defect, the SDK registers one that answers
    `[]` -- so that assertion passes on the unfixed tree and proves nothing.
    What has to hold is that anything advertised is a surface Hangar actually
    puts content into.

    Enumerated by capability rather than by name, so a future capability added
    without content fails here too, and so #889 flips prompts/resources back on
    by registering real content rather than by editing this test.
    """
    from mcp_hangar.server.bootstrap import build_serving_mcp_server

    mcp = build_serving_mcp_server()
    advertised = _capabilities(mcp)

    populated = {
        "tools": lambda: bool(mcp._tool_manager.list_tools()),
        "prompts": lambda: bool(mcp._prompt_manager.list_prompts()),
        "resources": lambda: bool(mcp._resource_manager.list_resources() or mcp._resource_manager.list_templates()),
    }
    for capability, has_content in populated.items():
        if capability in advertised:
            assert has_content(), f"advertised {capability!r} while serving nothing under it"

    # The concrete regression, stated outright so the failure names itself.
    assert "prompts" not in advertised
    assert "resources" not in advertised
