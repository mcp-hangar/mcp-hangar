"""A stale name is told to list again, over the app ``serve --http`` serves (#1368).

The unit tests drive the two handlers with a stand-in request context. On this
front door that shape has hidden fail-opens before: the identity is re-bound per
request, in a task the ASGI wrapper does not own, and the SDK lists tools by
itself before it dispatches a call (#1049). So everything here goes over the real
streamable-HTTP transport, through `_front_door_harness`:

* The composition ``serve --http`` serves, behind the authentication layer it
  mounts. Each tenant authenticates with its own API key.
* One real upstream, started through the command bus, whose catalogue lands in
  the projection registry through the ``McpServerStarted`` handler.
* The projection changes for real: a withdrawal through the registry, a policy
  edit through the resolver, and a fleet in which the tool does not exist at all.

Naming: neutral placeholders only (store, read_item, write_item, tenant:a, tenant:b).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.fastmcp_server import flat_tool_projection, served_tool_names
from mcp_hangar.fastmcp_server.served_tool_names import ServedNames
from tests.integration._front_door_harness import (
    LEGACY as _LEGACY,
    METHOD_NOT_FOUND,
    SERVER,
    TENANT_A,
    TENANT_B,
    front_door as _front_door,
    jsonrpc as _jsonrpc,
)

REASON = {"reason": "projection_changed"}


def _ordinary(name: str) -> dict[str, Any]:
    """The ``-32601`` every call to a name the caller cannot call has always had."""
    return {"code": METHOD_NOT_FOUND, "message": f"Tool '{name}' not found"}


@pytest.fixture(autouse=True)
def _fresh_memory(monkeypatch) -> None:
    monkeypatch.setattr(served_tool_names, "SERVED", ServedNames())


class TestTheFullSequence:
    def test_list_change_stale_call_relist_and_the_fresh_list_is_callable(self) -> None:
        """List, the projection changes, call the stale name, get the reason, list again, call."""
        with _front_door(("read_item", "write_item"), {TENANT_A: ("write_item",)}) as front_door:
            assert front_door.names(TENANT_A) == ["write_item"]

            # The projection changes under the connected client: an operator edits its policy.
            get_tool_access_resolver().set_standalone_member_policy(
                SERVER, TENANT_A, ToolAccessPolicy(allow_list=("read_item",))
            )

            assert front_door.error(TENANT_A, "write_item") == {**_ordinary("write_item"), "data": REASON}

            assert front_door.names(TENANT_A) == ["read_item"]
            result = front_door.result(TENANT_A, "read_item")
            assert result["content"] == [{"type": "text", "text": "did read_item"}]
            assert front_door.upstream.called == ["read_item"]

    def test_a_name_that_really_is_gone_is_the_ordinary_error_after_listing_again(self) -> None:
        with _front_door(("read_item", "write_item")) as front_door:
            assert front_door.names(TENANT_A) == ["read_item", "write_item"]

            get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)

            assert front_door.error(TENANT_A, "write_item") == {**_ordinary("write_item"), "data": REASON}
            assert front_door.names(TENANT_A) == ["read_item"]
            assert front_door.error(TENANT_A, "write_item") == _ordinary("write_item")
            assert front_door.upstream.called == []

    def test_a_handshake_era_client_gets_the_reason_too(self) -> None:
        """The legacy era answers on the SSE framing, through the front-door wrap rather than the modern entry."""
        with _front_door(("read_item", "write_item")) as front_door:
            assert front_door.names(TENANT_A, era=_LEGACY) == ["read_item", "write_item"]

            get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)

            assert front_door.error(TENANT_A, "write_item", era=_LEGACY) == {**_ordinary("write_item"), "data": REASON}
            assert front_door.names(TENANT_A, era=_LEGACY) == ["read_item"]
            assert front_door.error(TENANT_A, "write_item", era=_LEGACY) == _ordinary("write_item")


class TestDeniedAndAbsentStayIndistinguishable:
    """#905: a caller must not learn that a name exists for someone else."""

    def test_tenant_b_cannot_tell_a_tool_it_is_denied_from_one_that_exists_nowhere(self) -> None:
        # Tenant A holds write_item and has it withdrawn. Tenant B is denied it by
        # policy and so was never served it.
        with _front_door(("read_item", "write_item"), {TENANT_B: ("read_item",)}) as front_door:
            assert front_door.names(TENANT_A) == ["read_item", "write_item"]
            assert front_door.names(TENANT_B) == ["read_item"]
            get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)

            withdrawn_from_a = front_door.call(TENANT_A, "write_item", request_id=7)
            denied_to_b = front_door.call(TENANT_B, "write_item", request_id=7)
            never_existed_for_b = front_door.call(TENANT_B, "no_such_item", request_id=7)

        # The same caller, the same configuration and the same history, in a fleet
        # where write_item exists for nobody.
        with _front_door(("read_item",), {TENANT_B: ("read_item",)}) as front_door:
            assert front_door.names(TENANT_B) == ["read_item"]
            absent_for_b = front_door.call(TENANT_B, "write_item", request_id=7)

        # The full wire answer, byte for byte: status, framing and body.
        assert denied_to_b.status_code == absent_for_b.status_code
        assert denied_to_b.headers["content-type"] == absent_for_b.headers["content-type"]
        assert denied_to_b.content == absent_for_b.content
        assert _jsonrpc(denied_to_b) == _jsonrpc(absent_for_b)

        # Denied is the ordinary error, shaped exactly like one for a name nobody has.
        assert _jsonrpc(denied_to_b)["error"] == _ordinary("write_item")
        assert _jsonrpc(never_existed_for_b)["error"] == _ordinary("no_such_item")

        # Only the caller that was served the name hears that its list is stale.
        assert _jsonrpc(withdrawn_from_a)["error"] == {**_ordinary("write_item"), "data": REASON}

    def test_the_reason_names_no_upstream_no_tool_and_no_count(self) -> None:
        with _front_door(("read_item", "write_item"), {TENANT_B: ("read_item",)}) as front_door:
            front_door.names(TENANT_A)
            front_door.names(TENANT_B)
            get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)

            data = front_door.error(TENANT_A, "write_item")["data"]

        assert data == REASON
        carried = json.dumps(data)
        for named in (SERVER, "write_item", "read_item", TENANT_A, TENANT_B, "upstream"):
            assert named not in carried, f"the reason carries {named!r}"
        assert not any(character.isdigit() for character in carried), f"the reason carries a number: {carried}"


class TestOnlyAListingTheClientReceivedCounts:
    def test_the_sdks_own_listing_before_a_call_is_not_remembered(self, monkeypatch) -> None:
        """A 2026-07-28 call with arguments makes the SDK list first (#1049). The client received nothing."""
        listings: list[str | None] = []
        generate = flat_tool_projection.generate_projection

        def _counting(tenant_id: str | None) -> Any:
            listings.append(tenant_id)
            return generate(tenant_id)

        monkeypatch.setattr(flat_tool_projection, "generate_projection", _counting)

        with _front_door(("read_item", "write_item")) as front_door:
            assert front_door.result(TENANT_A, "read_item", {"x": "1"})["content"] == [
                {"type": "text", "text": "did read_item"}
            ]
            assert listings == [TENANT_A], "the SDK did not list before the call, so this test would prove nothing"

            get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)

            assert front_door.error(TENANT_A, "write_item") == _ordinary("write_item")
            assert len(served_tool_names.SERVED) == 0
