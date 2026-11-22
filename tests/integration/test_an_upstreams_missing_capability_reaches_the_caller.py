"""An upstream's ``-32021`` on an HTTP 400 is the front-door caller's protocol error when true of it (#2315).

Over the real streamable-HTTP transport (``_front_door_harness``). The upstream
answers one tool with HTTP 400 and ``MissingRequiredClientCapabilityError``
(``-32021``), as 2026-07-28 says a server that needs an undeclared capability
must. Before #2315 the client turned every 400 into ``-32000 HTTP error: 400``
and the front door answered a tool result, ``isError: true``, on HTTP 200, so
the caller could not tell a missing capability from a broken upstream.

The caller is shown the upstream's ``-32021`` only when every capability it
names is one the caller did not declare. Hangar does not forward the caller's
declaration (it never declares sampling upstream), so an upstream says
``-32021`` to a caller that did declare sampling too, and that caller keeps the
tool error. So do a legacy caller, an upstream naming Hangar's own namespace,
and a ``-32021`` without ``requiredCapabilities``. Only ``requiredCapabilities``
rides in the relayed ``data``.

Naming: neutral placeholders only (needs_sampling, tenant:a).
"""

from __future__ import annotations

import io
import json
from typing import Any, ClassVar

import httpx

from tests.integration._front_door_harness import LEGACY, MODERN, TENANT_A, Upstream, front_door, jsonrpc

TOOL = "needs_sampling"
MESSAGE = "Client must declare the sampling capability"


class _MissingCapabilityUpstream(Upstream):
    """Answers ``tools/call`` with HTTP 400 and the JSON-RPC error in ``answer``."""

    answer: ClassVar[dict[str, Any]] = {
        "code": -32021,
        "message": MESSAGE,
        "data": {"requiredCapabilities": {"sampling": {}}},
    }

    def do_POST(self) -> None:  # noqa: N802 -- http.server's handler name
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        request = json.loads(raw)
        if request.get("method") != "tools/call":
            self.rfile = io.BytesIO(raw)  # the base reads the same body and answers the rest
            super().do_POST()
            return
        self.called.append((request.get("params") or {}).get("name"))
        body = {"jsonrpc": "2.0", "id": request["id"], "error": self.answer}
        self._send(400, json.dumps(body).encode())


def _upstream(answer: dict[str, Any]) -> type[Upstream]:
    return type("_Answering", (_MissingCapabilityUpstream,), {"answer": answer})


def _tool_error(response: httpx.Response) -> str:
    """The text of the tool error the caller got, asserting that is what it got."""
    assert response.status_code == 200, response.text[:300]
    payload = jsonrpc(response)
    assert "error" not in payload, payload
    assert payload["result"]["isError"] is True, payload
    return str(payload["result"]["content"][0]["text"])


def test_a_caller_that_declared_nothing_gets_the_upstreams_minus_32021_on_a_400() -> None:
    with front_door((TOOL,), upstream_class=_MissingCapabilityUpstream) as door:
        response = door.call(TENANT_A, TOOL, request_id=7)

        assert response.status_code == 400, response.text[:300]
        payload = jsonrpc(response)
        assert payload["id"] == 7
        assert payload["error"] == {
            "code": -32021,
            "message": MESSAGE,
            "data": {"requiredCapabilities": {"sampling": {}}},
        }
        assert door.upstream.called == [TOOL]


def test_only_required_capabilities_ride_in_the_relayed_data() -> None:
    answer = {
        "code": -32021,
        "message": MESSAGE,
        "data": {"requiredCapabilities": {"sampling": {}}, "trace": "upstream-internal-detail"},
        "extra": "not a JSON-RPC error field",
    }
    with front_door((TOOL,), upstream_class=_upstream(answer)) as door:
        payload = jsonrpc(door.call(TENANT_A, TOOL))

        assert payload["error"]["data"] == {"requiredCapabilities": {"sampling": {}}}
        assert "extra" not in payload["error"]


def test_a_caller_that_declared_the_capability_keeps_the_tool_error() -> None:
    """Hangar never declares sampling upstream; telling this caller to declare it would be false."""
    with front_door((TOOL,), upstream_class=_MissingCapabilityUpstream) as door:
        text = _tool_error(door.call(TENANT_A, TOOL, capabilities={"sampling": {}}))

        assert MESSAGE in text


def test_a_named_extension_the_caller_declared_keeps_the_tool_error() -> None:
    answer = {"code": -32021, "message": MESSAGE, "data": {"requiredCapabilities": {"extensions": {"x.example/a": {}}}}}
    with front_door((TOOL,), upstream_class=_upstream(answer)) as door:
        _tool_error(door.call(TENANT_A, TOOL, capabilities={"extensions": {"x.example/a": {}}}))

        assert door.call(TENANT_A, TOOL, capabilities={"extensions": {"x.example/b": {}}}).status_code == 400


def test_a_legacy_caller_keeps_the_tool_error() -> None:
    """``-32021`` is not a code of the handshake revisions."""
    with front_door((TOOL,), upstream_class=_MissingCapabilityUpstream) as door:
        payload = jsonrpc(door.call(TENANT_A, TOOL, era=LEGACY))

        assert "error" not in payload, payload
        assert payload["result"]["isError"] is True


def test_an_upstream_naming_hangars_namespace_is_not_relayed() -> None:
    answer = {
        "code": -32021,
        "message": "Declare the approval extension",
        "data": {"requiredCapabilities": {"extensions": {"io.mcp-hangar/approval": {}}}},
    }
    with front_door((TOOL,), upstream_class=_upstream(answer)) as door:
        _tool_error(door.call(TENANT_A, TOOL))


def test_a_minus_32021_without_required_capabilities_keeps_the_tool_error() -> None:
    for data in ({}, {"requiredCapabilities": ["sampling"]}, {"requiredCapabilities": {}}):
        answer = {"code": -32021, "message": MESSAGE, "data": data}
        with front_door((TOOL,), upstream_class=_upstream(answer)) as door:
            _tool_error(door.call(TENANT_A, TOOL))


def test_another_protocol_error_on_a_400_keeps_the_tool_error_with_the_upstreams_message() -> None:
    """Hangar builds the envelope and headers the upstream judges: their errors are not the caller's."""
    for code in (-32602, -32020, -32600):
        answer = {"code": code, "message": f"upstream refused with {code}"}
        with front_door((TOOL,), upstream_class=_upstream(answer)) as door:
            text = _tool_error(door.call(TENANT_A, TOOL, era=MODERN))

            assert f"upstream refused with {code}" in text
            assert "HTTP error: 400" not in text
