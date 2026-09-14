"""A stdio upstream that refuses the MCP handshake and keeps running.

It answers every request with a JSON-RPC error, ``initialize`` included, and
then waits for the next line instead of exiting. That is the case a failed
start has to clean up after: the start fails while the process stays alive, so
a client the gateway does not close leaves the process running.

Its stderr is pointed at /dev/null before anything else. The gateway's startup
diagnostics read the stderr pipe to EOF, and a live process holding that pipe
open would keep the read waiting for as long as the process ran.
"""

import json
import os
import sys


def main() -> None:
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 2)
    os.close(devnull)

    while True:
        line = sys.stdin.readline()
        if not line:
            return
        request = json.loads(line)
        if "id" not in request:
            continue
        reply = {
            "jsonrpc": "2.0",
            "id": request["id"],
            "error": {"code": -32603, "message": "handshake refused"},
        }
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
