"""A stdio MCP upstream with one tool, ``echo``, whose metadata comes from the environment.

``UPSTREAM_TOOL`` renames the tool. ``UPSTREAM_EXTRAS`` is a JSON object
merged into its definition, so a test can pin ``echo`` and then start the
same upstream with a ``title``, ``annotations`` or ``_meta`` the pin's digest
does not cover. Plain JSON-RPC lines, so nothing depends on SDK internals. Run
as a script:

    UPSTREAM_EXTRAS='{"title": "Echo"}' python tests/annotated_upstream.py
"""

from __future__ import annotations

import json
import os
import sys

TOOL = {
    "name": "echo",
    "description": "Echo the text back.",
    "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
}


def answer(request: dict) -> dict:
    method = request.get("method")
    if method == "initialize":
        result: dict = {
            "protocolVersion": request["params"].get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "annotated", "version": "0"},
        }
    elif method == "tools/list":
        extras = json.loads(os.environ.get("UPSTREAM_EXTRAS") or "{}")
        result = {"tools": [{**TOOL, "name": os.environ.get("UPSTREAM_TOOL", "echo"), **extras}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": request["params"]["arguments"].get("text", "")}]}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request["id"], "result": result}


def main() -> None:
    for line in sys.stdin:
        request = json.loads(line)
        if "id" in request:
            print(json.dumps(answer(request)), flush=True)


if __name__ == "__main__":
    main()
