"""A stdio upstream that fails its start and then goes quiet without exiting.

It answers ``initialize``, answers ``tools/list`` with a JSON-RPC error, and then
stays alive, reading nothing and writing nothing, to stderr least of all. Its
pipes never reach EOF while it lives, so a gateway that reads one to the end on
the failed start waits for as long as this process runs.

It runs until its parent goes away, so a test that is stopped early leaves no
orphan behind. When ``SILENT_PROVIDER_PID_FILE`` names a file, it writes its pid
there, so a test can check that the gateway terminated it.
"""

import json
import os
import sys
import time


def _reply(request_id, body):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, **body}) + "\n")
    sys.stdout.flush()


def main():
    pid_file = os.environ.get("SILENT_PROVIDER_PID_FILE")
    if pid_file:
        with open(pid_file, "w", encoding="utf-8") as out:
            out.write(str(os.getpid()))

    parent = os.getppid()
    while True:
        line = sys.stdin.readline()
        if not line:
            return
        request = json.loads(line)
        method = request.get("method")
        if method == "initialize":
            _reply(
                request.get("id"),
                {"result": {"protocolVersion": "2024-11-05", "serverInfo": {"name": "silent", "version": "0"}}},
            )
        elif method == "tools/list":
            _reply(request.get("id"), {"error": {"code": -32603, "message": "tools unavailable"}})
            break

    while os.getppid() == parent:
        time.sleep(0.2)


if __name__ == "__main__":
    main()
