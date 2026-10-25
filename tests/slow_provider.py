"""A raw JSON-RPC stdio MCP server with one slow tool, for the HTTP stop tests (#2097).

``sleep`` appends ``started <pid>`` to the log file named on the command line,
sleeps for its ``seconds`` argument, then appends ``completed`` and answers.
The test reads the log to know the call is in flight upstream, and how many
times it completed there.
"""

import json
import os
import sys
import time
from pathlib import Path

LOG = Path(sys.argv[1])
TOOL = {
    "name": "sleep",
    "description": "Sleep, then record that the call completed.",
    "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number"}}, "required": ["seconds"]},
}


def note(line: str) -> None:
    with LOG.open("a") as log:
        log.write(line + "\n")


def reply(request_id: object, result: dict) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    message = json.loads(raw)
    request_id, method = message.get("id"), message.get("method")
    if method == "initialize":
        version = message["params"].get("protocolVersion", "2025-06-18")
        reply(request_id, {"protocolVersion": version, "capabilities": {"tools": {}}, "serverInfo": {"name": "slow"}})
    elif method == "tools/list":
        reply(request_id, {"tools": [TOOL]})
    elif method == "tools/call":
        note(f"started {os.getpid()}")
        seconds = message["params"]["arguments"]["seconds"]
        time.sleep(seconds)
        note("completed")
        reply(request_id, {"content": [{"type": "text", "text": f"slept {seconds}"}]})
    elif request_id is not None:
        reply(request_id, {})
