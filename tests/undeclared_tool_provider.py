"""A stdio MCP upstream that serves one tool more than its server declares.

It serves ``add``, which the tests declare in ``capabilities.tools.expected_tools``,
and ``exfiltrate``, which they do not. Each process appends a ``start`` line with
its pid to the file ``UNDECLARED_PROVIDER_RECORD`` names, and one line for every
request it receives. A test then reads what reached the upstream and which
processes ran it, instead of trusting the gateway's account of either.

Used by tests/integration/test_a_capability_block_stops_the_call_that_found_it.py
and its harness. Not collected by pytest.
"""

import json
import os
import sys

TOOLS = [
    {
        "name": "add",
        "description": "Add two numbers",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "exfiltrate",
        "description": "Served, and not declared by the server that serves it",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    },
]


def _record(entry):
    path = os.environ.get("UNDECLARED_PROVIDER_RECORD")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as record:
        record.write(json.dumps({"pid": os.getpid(), **entry}) + "\n")


def _answer(method, params):
    if method == "initialize":
        return {
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "undeclared-tool-provider", "version": "0"},
            }
        }
    if method == "tools/list":
        return {"result": {"tools": TOOLS}}
    if method == "tools/call":
        return {"result": {"content": [{"type": "text", "text": f"{params.get('name')} ran"}]}}
    if method == "shutdown":
        return {"result": {}}
    return {"error": {"code": -32601, "message": f"Unknown method: {method}"}}


def main():
    _record({"event": "start"})
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        if not line.strip():
            continue
        request = json.loads(line)
        method = request.get("method")
        params = request.get("params") or {}
        _record({"event": "request", "method": method, "tool": params.get("name") if method == "tools/call" else None})
        if "id" not in request:
            continue  # a notification: no answer
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request["id"], **_answer(method, params)}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
