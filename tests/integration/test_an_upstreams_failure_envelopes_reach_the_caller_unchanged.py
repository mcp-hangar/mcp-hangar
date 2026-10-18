"""An upstream's structured failure and denial envelopes pass through Hangar unchanged (#1900).

Two drafts put a structured envelope in the ``_meta`` of a tool result, and both bind an
intermediary to relay it as it was sent: SEP-3313 (``io.modelcontextprotocol/toolFailure``,
with a retry hint and a correlation id) and SEP-2643 (an authorization denial with a
remediation hint). Hangar used to turn any upstream ``isError: true`` result into a bare
text error, so neither envelope, nor the structured content beside it, reached the caller.

The keys below are representative: the rule under test is that everything the upstream sent
under ``io.modelcontextprotocol/`` arrives byte for byte, whatever the key is called.

Hangar never substitutes an upstream's remediation hint with one of its own. A refusal
Hangar raises itself carries Hangar's reason; an upstream's answer carries the upstream's.

Everything goes over the real streamable-HTTP transport (``_front_door_harness``).

Naming: neutral placeholders only (store, guarded_item, tenant:a).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from tests.integration._front_door_harness import SERVER, TENANT_A, FrontDoor, Upstream, front_door, jsonrpc

TOOL = "guarded_item"
FAILURE = {"class": "governance", "code": "quota_exceeded", "retryAfterMs": 2000, "correlationId": "c-123"}
DENIAL = {"reason": "scope", "remediation": {"hint": "ask the owner of store for read access"}}
ENVELOPES = {"io.modelcontextprotocol/toolFailure": FAILURE, "io.modelcontextprotocol/authorizationDenial": DENIAL}
STRUCTURED = {"limit": 10, "used": 10}
ANSWER = {
    "content": [{"type": "text", "text": "quota exceeded"}, {"type": "text", "text": "retry in 2s"}],
    "structuredContent": STRUCTURED,
    "isError": True,
    "_meta": ENVELOPES,
}


class FailingUpstream(Upstream):
    """Answers ``guarded_item`` with a failure that carries both envelopes."""

    def do_POST(self) -> None:  # noqa: N802 -- http.server's handler name
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if "id" not in request:
            self._send(202, b"")
            return
        method = request.get("method")
        params = request.get("params") or {}
        if method == "initialize":
            answer: dict[str, Any] = {
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "upstream", "version": "0"},
                }
            }
        elif method == "tools/list":
            answer = {"result": {"tools": [{"name": TOOL, "inputSchema": {"type": "object"}}]}}
        elif method == "tools/call":
            self.called.append(params.get("name"))
            answer = {"result": ANSWER}
        else:
            answer = {"error": {"code": -32601, "message": f"Unknown method: {method}"}}
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": request["id"], **answer}).encode())


@pytest.fixture(params=["truncation off", "truncation on"])
def truncation(request: pytest.FixtureRequest) -> Iterator[None]:
    from mcp_hangar.server.bootstrap.truncation import init_truncation

    if request.param == "truncation on":
        assert init_truncation(
            {"truncation": {"enabled": True, "max_batch_size_bytes": 200, "min_per_response_bytes": 50}}
        )
    try:
        yield
    finally:
        init_truncation({})


def _serve(topology: str) -> Any:
    return front_door((TOOL,), topology=topology, upstream_class=FailingUpstream)


def _batch_entry(door: FrontDoor) -> dict[str, Any]:
    calls = [{"mcp_server": SERVER, "tool": TOOL, "arguments": {}}]
    result = jsonrpc(door.call(TENANT_A, "hangar_call", {"calls": calls}))["result"]
    body = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    assert len(body["results"]) == 1, body
    return dict(body["results"][0])


def _bytes(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def test_a_flat_call_returns_the_upstreams_failure_as_sent(truncation):
    with _serve("front_door") as door:
        result = jsonrpc(door.call(TENANT_A, TOOL))["result"]

    assert result["isError"] is True
    assert result["content"] == ANSWER["content"]
    assert result["structuredContent"] == STRUCTURED
    for key, envelope in ENVELOPES.items():
        assert _bytes(result["_meta"][key]) == _bytes(envelope)


def test_a_hangar_call_returns_the_upstreams_failure_as_sent(truncation):
    with _serve("egress") as door:
        entry = _batch_entry(door)

    assert entry["success"] is False
    assert entry["error"] == "tool_error: quota exceeded; retry in 2s"
    assert entry["result"] is None
    assert _bytes(entry["upstream_result"]) == _bytes(ANSWER)


def test_a_failure_hangar_raises_itself_carries_no_upstream_result():
    """Nothing is attributed to the upstream that it did not send."""
    from mcp_hangar.domain.exceptions import ToolInvocationError
    from mcp_hangar.server.tools.batch.executor import _upstream_result_of

    assert (
        _upstream_result_of(ToolInvocationError(SERVER, "tool_error: x", {"is_error": True, "result": ANSWER}))
        == ANSWER
    )
    assert _upstream_result_of(ToolInvocationError(SERVER, "No response from mcp_server")) is None
    assert _upstream_result_of(ToolInvocationError(SERVER, "tool_error: x", {"is_error": True})) is None
    assert _upstream_result_of(RuntimeError("boom")) is None
    assert _upstream_result_of(None) is None
