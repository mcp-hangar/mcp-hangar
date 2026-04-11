"""Simple mock MCP provider for testing."""

import json
import sys


def main():
    """Run a simple JSON-RPC server for testing."""
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break

            request = json.loads(line)
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params", {})

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
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {
                                "name": "add",
                                "description": "Add two numbers",
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
            elif method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments", {})

                if tool_name == "add":
                    result = arguments["a"] + arguments["b"]
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"result": result},
                    }
                elif tool_name == "subtract":
                    result = arguments["a"] - arguments["b"]
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"result": result},
                    }
                elif tool_name == "multiply":
                    result = arguments["a"] * arguments["b"]
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"result": result},
                    }
                elif tool_name == "divide":
                    if arguments["b"] == 0:
                        response = {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -1, "message": "division by zero"},
                        }
                    else:
                        result = arguments["a"] / arguments["b"]
                        response = {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": {"result": result},
                        }
                elif tool_name == "power":
                    result = arguments["base"] ** arguments["exponent"]
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"result": result},
                    }
                elif tool_name == "echo":
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"message": arguments["message"]},
                    }
                elif tool_name == "error":
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -1,
                            "message": "Intentional error for testing",
                        },
                    }
                else:
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": f"Unknown tool: {tool_name}",
                        },
                    }
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

        except Exception:
            # Silent error handling
            break


if __name__ == "__main__":
    main()
