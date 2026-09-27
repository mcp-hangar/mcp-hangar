"""A front-door flat call gets its result whole on the app ``serve --http`` serves (#1609).

A flat ``tools/call`` is one call whose caller expects the upstream's result as
it was sent, and that surface has no continuation tool to fetch a cut part
with. With batch truncation on, the flat call was cut like a ``hangar_call``
batch member, and the cut result was no valid tool result: the caller got
``INVALID_RESULT_TEXT`` instead of its answer. Over the per-call size cap, the
executor dropped the result and the caller got an empty success.

The flat call now takes its result whole, as the facade's ``invoke`` does
(#1453). ``hangar_call`` is cut exactly as before, under both rules.

Everything goes over the real streamable-HTTP transport (``_front_door_harness``).
Nothing on the call path is patched except, in the size-cap tests, the cap
itself, lowered so a test result can cross it.

Naming: neutral placeholders only (store, big_item, tenant:a).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from tests.integration._front_door_harness import SERVER, TENANT_A, FrontDoor, Upstream, front_door, jsonrpc

TOOL = "big_item"
TEXT_LENGTH = 40_000
TEXT = "start-" + "y" * TEXT_LENGTH + "-end"
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"length": {"type": "integer"}, "text": {"type": "string"}},
    "required": ["length", "text"],
}
#: A batch budget the result is twice over.
TRUNCATION = {"enabled": True, "max_batch_size_bytes": 20_000, "min_per_response_bytes": 1_000}
#: A per-call size cap the result is over.
LOW_CAP = 30_000


class BigUpstream(Upstream):
    """Answers ``big_item`` with a large result that its output schema describes."""

    def do_POST(self) -> None:  # noqa: N802 -- http.server's handler name
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if "id" not in request:
            self._send(202, b"")
            return
        method = request.get("method")
        params = request.get("params") or {}
        definition = {"name": TOOL, "inputSchema": {"type": "object"}, "outputSchema": OUTPUT_SCHEMA}
        if method == "initialize":
            answer: dict[str, Any] = {
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "upstream", "version": "0"},
                }
            }
        elif method == "tools/list":
            answer = {"result": {"tools": [definition]}}
        elif method == "tools/call":
            self.called.append(params.get("name"))
            structured = {"length": len(TEXT), "text": TEXT}
            answer = {"result": {"content": [{"type": "text", "text": TEXT}], "structuredContent": structured}}
        else:
            answer = {"error": {"code": -32601, "message": f"Unknown method: {method}"}}
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": request["id"], **answer}).encode())


@pytest.fixture
def truncation() -> Iterator[None]:
    from mcp_hangar.server.bootstrap.truncation import init_truncation

    assert init_truncation({"truncation": TRUNCATION}) is not None
    try:
        yield
    finally:
        init_truncation({})


def _serve(topology: str) -> Any:
    return front_door((TOOL,), topology=topology, upstream_class=BigUpstream)


def _batch_entry(door: FrontDoor) -> dict[str, Any]:
    """The one result of a ``hangar_call`` for ``big_item``, as the caller reads it."""
    calls = [{"mcp_server": SERVER, "tool": TOOL, "arguments": {}}]
    payload = jsonrpc(door.call(TENANT_A, "hangar_call", {"calls": calls}))
    result = payload["result"]
    body = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    assert len(body["results"]) == 1, body
    return dict(body["results"][0])


def _assert_whole(result: dict[str, Any]) -> None:
    assert not result.get("isError"), json.dumps(result)[:300]
    assert result["content"] == [{"type": "text", "text": TEXT}], json.dumps(result)[:300]
    assert result["structuredContent"] == {"length": len(TEXT), "text": TEXT}, json.dumps(result)[:300]


def test_a_flat_call_is_not_cut_by_batch_truncation(truncation):
    with _serve("front_door") as door:
        result = door.result(TENANT_A, TOOL)

        _assert_whole(result)
        assert door.upstream.called == [TOOL]


def test_no_continuation_is_stored_for_a_flat_call(truncation):
    from mcp_hangar.server.bootstrap.truncation import get_response_cache

    with _serve("front_door") as door:
        door.result(TENANT_A, TOOL)

        assert get_response_cache()._cache == {}  # type: ignore[union-attr]


def test_a_hangar_call_over_the_budget_is_still_cut(truncation):
    with _serve("egress") as door:
        entry = _batch_entry(door)

        assert entry["success"] is True, entry
        assert entry["truncated"] is True, entry
        assert entry["continuation_id"], entry
        assert TEXT not in json.dumps(entry)


def test_a_flat_call_over_the_per_call_size_cap_is_returned_whole():
    """The cap is not a bound for a flat call: the result is in memory before it applies.

    Under the cap the caller got ``{}`` -- an empty success -- and no word of
    what it lost. See `_flat_call_tool`.
    """
    with patch("mcp_hangar.server.tools.batch.executor.MAX_RESPONSE_SIZE_BYTES", LOW_CAP), _serve("front_door") as door:
        _assert_whole(door.result(TENANT_A, TOOL))


def test_a_hangar_call_over_the_per_call_size_cap_is_still_dropped():
    with patch("mcp_hangar.server.tools.batch.executor.MAX_RESPONSE_SIZE_BYTES", LOW_CAP), _serve("egress") as door:
        entry = _batch_entry(door)

        assert entry["truncated"] is True, entry
        assert entry["truncated_reason"] == "response_size_exceeded", entry
        assert entry.get("result") is None, entry
