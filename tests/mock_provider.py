"""Simple mock MCP provider for testing."""

import json
import os
import sys


def _record_trace_carrier(method, params):
    """Append the ``_meta.traceparent`` this request carried to ``MOCK_PROVIDER_RECORD``.

    Off unless the variable names a file. It lets a test read the outbound trace
    carrier the gateway actually wrote over stdio, rather than trust the code
    that writes it (tests/integration/_trace_harness.py).
    """
    path = os.environ.get("MOCK_PROVIDER_RECORD")
    if not path:
        return
    meta = params.get("_meta") if isinstance(params, dict) else None
    with open(path, "a", encoding="utf-8") as record:
        record.write(json.dumps({"method": method, "traceparent": (meta or {}).get("traceparent")}) + "\n")


def _tools_list_response(request_id):
    """Answer ``tools/list``: the tools, or an error while ``MOCK_TOOLS_LIST_FAILS_WHILE`` names a file that exists.

    A health check is a ``tools/list``, so a test fails and passes checks one
    at a time by creating and removing the file
    (tests/integration/_group_recovery_harness.py).
    """
    flag = os.environ.get("MOCK_TOOLS_LIST_FAILS_WHILE")
    if flag and os.path.exists(flag):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": "tools/list failing"}}
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "tools": [
                {
                    "name": "add",
                    # Env-driven so a test can change what this
                    # server serves between two runs -- which is
                    # what schema drift is (tests/integration
                    # test_pin_writes_and_detects_drift.py).
                    "description": os.environ.get("MOCK_ADD_DESCRIPTION", "Add two numbers"),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "a": {"type": "number"},
                            "b": {"type": "number"},
                        },
                        "required": ["a", "b"],
                    },
                },
                {
                    "name": "subtract",
                    "description": "Subtract two numbers",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "a": {"type": "number"},
                            "b": {"type": "number"},
                        },
                        "required": ["a", "b"],
                    },
                },
                {
                    "name": "multiply",
                    "description": "Multiply two numbers",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "a": {"type": "number"},
                            "b": {"type": "number"},
                        },
                        "required": ["a", "b"],
                    },
                },
                {
                    "name": "divide",
                    "description": "Divide two numbers",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "a": {"type": "number"},
                            "b": {"type": "number"},
                        },
                        "required": ["a", "b"],
                    },
                },
                {
                    "name": "power",
                    "description": "Raise to power",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "base": {"type": "number"},
                            "exponent": {"type": "number"},
                        },
                        "required": ["base", "exponent"],
                    },
                },
                {
                    "name": "echo",
                    "description": "Echo a message",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                    },
                },
            ]
        },
    }


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _call_tool(request_id, tool_name, arguments):
    """One tool's answer.

    Raises ``KeyError`` or ``TypeError`` for arguments the tool cannot read, and
    ``ArithmeticError`` for a sum it cannot do.
    """
    if tool_name == "add":
        return _result(request_id, {"result": arguments["a"] + arguments["b"]})
    if tool_name == "subtract":
        return _result(request_id, {"result": arguments["a"] - arguments["b"]})
    if tool_name == "multiply":
        return _result(request_id, {"result": arguments["a"] * arguments["b"]})
    if tool_name == "divide":
        if arguments["b"] == 0:
            return _error(request_id, -1, "division by zero")
        return _result(request_id, {"result": arguments["a"] / arguments["b"]})
    if tool_name == "power":
        return _result(request_id, {"result": arguments["base"] ** arguments["exponent"]})
    if tool_name == "echo":
        return _result(request_id, {"message": arguments["message"]})
    if tool_name == "error":
        return _error(request_id, -1, "Intentional error for testing")
    return _error(request_id, -32601, f"Unknown tool: {tool_name}")


def _tools_call_response(request_id, params):
    """Answer ``tools/call``.

    While ``MOCK_TOOLS_CALL_FAILS_WHILE`` names a file that exists, every call
    fails with a JSON-RPC server error (-32000): the upstream is up and not
    working. That is how tests/integration/_group_recovery_harness.py fails a
    group member.

    Otherwise the tool answers. Arguments it cannot read answer invalid params
    (-32602). An arithmetic error, such as ``power`` of 0 to a negative
    exponent, is the tool's own error: a result with ``isError: true``. A
    division by zero keeps its JSON-RPC error with the application code -1.
    """
    flag = os.environ.get("MOCK_TOOLS_CALL_FAILS_WHILE")
    if flag and os.path.exists(flag):
        return _error(request_id, -32000, "tools/call failing")
    try:
        return _call_tool(request_id, params.get("name"), params.get("arguments", {}))
    except (KeyError, TypeError) as e:
        return _error(request_id, -32602, f"Invalid params: {e!r}")
    except ArithmeticError as e:
        return _result(request_id, {"content": [{"type": "text", "text": str(e)}], "isError": True})


def _announce():
    """Write ``MOCK_STDERR_BANNER`` to stderr once, at startup, when it is set.

    Off unless the variable holds a line. A server's log buffer is filled by the
    gateway's stderr reader, so this is how a test gets a line it can name into
    the log the ``/logs`` API serves (tests/integration/_added_server_logs_harness.py).
    """
    banner = os.environ.get("MOCK_STDERR_BANNER")
    if banner:
        print(banner, file=sys.stderr, flush=True)


def main():
    """Run a simple JSON-RPC server for testing."""
    _announce()
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break

            request = json.loads(line)
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params", {})
            _record_trace_carrier(method, params)

            if method == "initialize":
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "mock-provider", "version": "0.1.0"},
                    },
                }
            elif method == "tools/list":
                response = _tools_list_response(request_id)
            elif method == "tools/call":
                response = _tools_call_response(request_id, params)
            elif method == "shutdown":
                response = {"jsonrpc": "2.0", "id": request_id, "result": {}}
                print(json.dumps(response), flush=True)
                break
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Unknown method: {method}"},
                }

            print(json.dumps(response), flush=True)

        except Exception:  # noqa: BLE001
            # Silent error handling
            break


if __name__ == "__main__":
    main()
