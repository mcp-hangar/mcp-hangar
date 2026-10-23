"""An MCP server over HTTPS with one ``echo`` tool, for the two-replica tier (#1949).

    python _peer_key_server.py <certfile> <keyfile> <host> <port>

The gateway pins it by the key in ``keyfile`` (ADR-030). It listens on a host
address rather than loopback: the API holds an endpoint to its SSRF policy, and
no ``ssrf.trusted_networks`` entry can make loopback trusted.
"""

import sys

import uvicorn
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("peer-key-provider")


@mcp.tool(name="echo")
def echo(text: str) -> str:
    """Echo the text back."""
    return text


if __name__ == "__main__":
    certfile, keyfile, host, port = sys.argv[1:5]
    # The SDK answers 421 to a Host it was not built for, and its default is loopback.
    app = mcp.streamable_http_app(host=host)
    uvicorn.run(app, host=host, port=int(port), ssl_certfile=certfile, ssl_keyfile=keyfile, log_level="warning")
