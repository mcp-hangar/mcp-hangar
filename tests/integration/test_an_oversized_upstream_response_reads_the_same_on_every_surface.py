"""A response over the read limit fails the call the same way on every surface (#1613).

The app ``serve --http`` serves, over the real streamable-HTTP transport
(``_front_door_harness``), with one HTTP upstream whose ``big_item`` answers
with a body over the configured limit. The transport stops reading it, and:

* a front-door flat call gets a tool error (``isError``) with the
  ``ResponseTooLarge`` message, not an empty success;
* a ``hangar_call`` gets a failed entry with ``error_type: ResponseTooLarge``
  and the same message, not a dropped result with
  ``truncated_reason: response_size_exceeded``;
* the next call on the same upstream is served on both surfaces.

Naming: neutral placeholders only (store, read_item, big_item, tenant:a).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from mcp_hangar.response_limit import DEFAULT_MAX_RESPONSE_BYTES, set_default_max_response_bytes
from tests.integration._front_door_harness import SERVER, TENANT_A, FrontDoor, Upstream, front_door, jsonrpc

READ = "read_item"
BIG = "big_item"
LIMIT = 64 * 1024
MESSAGE = f"The upstream response exceeded the limit of {LIMIT} bytes and was not read."


class _BigUpstream(Upstream):
    """Answers ``big_item`` with a body four times the limit; everything else as the plain upstream."""

    def _send(self, status: int, body: bytes) -> None:
        if b'"did ' + BIG.encode() + b'"' in body:
            answer = json.loads(body)
            answer["result"]["content"][0]["text"] = "x" * (LIMIT * 4)
            body = json.dumps(answer).encode()
        super()._send(status, body)


@pytest.fixture(autouse=True)
def _small_limit() -> Iterator[None]:
    set_default_max_response_bytes(LIMIT)
    try:
        yield
    finally:
        set_default_max_response_bytes(DEFAULT_MAX_RESPONSE_BYTES)


def _flat(door: FrontDoor, tool: str) -> dict[str, Any]:
    payload = jsonrpc(door.call(TENANT_A, tool))
    assert "result" in payload, payload
    return dict(payload["result"])


def _hangar_call(door: FrontDoor, tool: str) -> dict[str, Any]:
    calls = [{"mcp_server": SERVER, "tool": tool, "arguments": {}}]
    payload = jsonrpc(door.call(TENANT_A, "hangar_call", {"calls": calls}))
    assert "result" in payload, payload
    (call,) = json.loads(payload["result"]["content"][0]["text"])["results"]
    return dict(call)


def test_a_flat_call_over_the_limit_is_a_tool_error_naming_the_limit() -> None:
    with front_door((READ, BIG), upstream_class=_BigUpstream) as door:
        result = _flat(door, BIG)

        assert result.get("isError") is True, result
        assert result["content"] == [{"type": "text", "text": MESSAGE}]
        assert _flat(door, READ)["content"][0]["text"] == f"did {READ}"


def test_a_hangar_call_over_the_limit_fails_with_response_too_large() -> None:
    with front_door((READ, BIG), topology="egress", upstream_class=_BigUpstream) as door:
        call = _hangar_call(door, BIG)

        assert call["success"] is False
        assert call["error_type"] == "ResponseTooLarge"
        assert call["error"] == MESSAGE
        assert "truncated_reason" not in call
        assert _hangar_call(door, READ)["success"] is True


def test_a_call_under_the_limit_is_served_whole_on_both_surfaces() -> None:
    set_default_max_response_bytes(LIMIT * 8)
    with front_door((BIG,), upstream_class=_BigUpstream) as door:
        assert _flat(door, BIG)["content"][0]["text"] == "x" * (LIMIT * 4)
    with front_door((BIG,), topology="egress", upstream_class=_BigUpstream) as door:
        call = _hangar_call(door, BIG)
        assert call["success"] is True
        assert call["result"]["content"][0]["text"] == "x" * (LIMIT * 4)
