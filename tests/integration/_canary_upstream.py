"""The upstream of the telemetry canary suite (#1535), and the canaries it answers with.

Run as a script it is a stdio MCP server: ``python _canary_upstream.py``. The
harness also imports it and serves the same ``answer()`` over HTTP, so the two
transports differ only in how the bytes travel. That is the claim the contract
(#1276, principle 7) makes and the suite checks.

A canary is a synthetic value nothing but the suite's inputs produce, one per
kind of input and per transport, so a canary found in a sink names the input
and the transport it came in on. Long kinds exceed every length bound the
contract sets, and end in a tail only an uncut copy carries.

Each upstream serves its tools under its transport's prefix (``stdio-note``,
``http-note``): on the front door the two catalogues share one namespace.
The stdio upstream appends every request's tool, arguments and ``_meta`` to the
file named by ``CANARY_UPSTREAM_RECORD``; the harness records the same, and the
HTTP headers, for the HTTP one.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

#: A fixed tag: a canary is the same on every run, so a failure reproduces.
_TAG = "7f3a"

#: Past the domain-event bound (4096), the log-field bound (2048) and the span
#: and audit attribute bound (256).
_LONG = 5000

TRANSPORTS = ("stdio", "http")

#: Every kind of input the suite injects. The long ones are free text an
#: upstream or an approver writes, which a sink may carry only bounded.
KINDS = (
    "argument",  # a tool argument under an ordinary key
    "secret_argument",  # a tool argument under a key the argument redactor treats as secret
    "result",  # the text of a successful result
    "is_error",  # the text of an ``isError: true`` result
    "rpc_error",  # an upstream's JSON-RPC error message
    "baggage",  # inbound W3C baggage, as an HTTP header and in ``_meta``
    "header",  # an inbound HTTP header Hangar has no reason to read
    "approver_reason",  # a human approver's reason for a denial
)
LONG_KINDS = frozenset({"is_error", "rpc_error", "approver_reason"})


def head(kind: str, transport: str) -> str:
    """What any copy of the canary carries, cut or not."""
    return f"CANARY-{kind.upper().replace('_', '')}-{transport.upper()}-{_TAG}"


def tail(kind: str, transport: str) -> str | None:
    """What only an uncut copy of a long canary carries; None for a short one."""
    return f"TAIL-{kind.upper().replace('_', '')}-{transport.upper()}-{_TAG}" if kind in LONG_KINDS else None


def canary(kind: str, transport: str) -> str:
    """The value injected for ``kind`` on ``transport``."""
    start, end = head(kind, transport), tail(kind, transport)
    if end is None:
        return start
    return start + "-" + "x" * (_LONG - len(start) - len(end) - 2) + "-" + end


#: The tools, each producing one canary. ``guarded`` is approval-listed by the harness.
TOOLS = ("note", "result_text", "is_error", "rpc_error", "guarded")

_STRING = {"type": "string"}


def prefix(transport: str) -> str:
    return f"{transport}-"


def tools_list(transport: str) -> list[dict[str, Any]]:
    schema = {"type": "object", "properties": {"note": _STRING, "api_token": _STRING}}
    return [{"name": prefix(transport) + t, "description": f"canary {t}", "inputSchema": schema} for t in TOOLS]


def call_result(tool: str, transport: str) -> dict[str, Any]:
    """``tools/call``'s answer body for unprefixed ``tool``: ``{"result": ...}`` or ``{"error": ...}``."""
    if tool == "result_text":
        return {"result": {"content": [{"type": "text", "text": canary("result", transport)}]}}
    if tool == "is_error":
        return {"result": {"isError": True, "content": [{"type": "text", "text": canary("is_error", transport)}]}}
    if tool == "rpc_error":
        return {"error": {"code": -32000, "message": canary("rpc_error", transport)}}
    return {"result": {"content": [{"type": "text", "text": "noted"}]}}


def answer(request: dict[str, Any], transport: str) -> dict[str, Any] | None:
    """The JSON-RPC response to ``request``; None for a notification."""
    if "id" not in request:
        return None
    method = request.get("method")
    params = request.get("params") or {}
    if method == "initialize":
        body: dict[str, Any] = {
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "canary-upstream", "version": "0"},
            }
        }
    elif method == "tools/list":
        body = {"result": {"tools": tools_list(transport)}}
    elif method == "tools/call":
        body = call_result(str(params.get("name")).removeprefix(prefix(transport)), transport)
    else:
        body = {"error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": request["id"], **body}


def seen(request: dict[str, Any]) -> dict[str, Any]:
    """What the suite keeps of a request the upstream received."""
    params = request.get("params") or {}
    return {
        "method": request.get("method"),
        "tool": params.get("name"),
        "arguments": params.get("arguments"),
        "meta": params.get("_meta"),
    }


def main() -> None:
    record = os.environ.get("CANARY_UPSTREAM_RECORD")
    for line in sys.stdin:
        request = json.loads(line)
        if record:
            with open(record, "a", encoding="utf-8") as out:
                out.write(json.dumps(seen(request)) + "\n")
        response = answer(request, "stdio")
        if response is not None:
            print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
