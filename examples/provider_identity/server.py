"""Identity-revealing stub backend for live group / canary verification.

Unlike ``examples/provider_math`` (whose tool results are anonymous), this stub
echoes *which* backend instance served a call. Each group member is launched as
a separate subprocess with a distinct identity -- passed as the first CLI
argument (or the ``PROVIDER_IDENTITY`` env var) -- and its ``whoami`` tool
returns that identity in the result. A live test can therefore observe the
member a ``hangar_call`` was routed to and assert group-invocation and canary
routing behaviour end to end.

It speaks MCP over **stdio** by default (the transport hangar uses for
``mode: subprocess`` members); override with ``MCP_TRANSPORT=streamable-http``.
"""

import os
import sys

from mcp.server.fastmcp import FastMCP

# Identity precedence: first CLI arg, then env var, then a placeholder.
_IDENTITY = (sys.argv[1] if len(sys.argv) > 1 else "") or os.environ.get("PROVIDER_IDENTITY", "unknown")

mcp = FastMCP(
    f"identity-provider[{_IDENTITY}]",
    host=os.environ.get("MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("MCP_PORT", "8080")),
    transport_security=None,
)


@mcp.tool(name="whoami")
def whoami() -> dict:
    """Return the identity of the backend member that served this call.

    Returns:
        Dictionary with a 'member' key naming the serving backend instance.
    """
    return {"member": _IDENTITY}


@mcp.tool(name="echo")
def echo(text: str) -> dict:
    """Echo text back, tagged with the serving member's identity.

    Args:
        text: Arbitrary text to echo.

    Returns:
        Dictionary with 'member' and 'text' keys.
    """
    return {"member": _IDENTITY, "text": text}


def main():
    """Run the identity provider.

    Defaults to stdio transport (subprocess mode). Override with
    MCP_TRANSPORT=streamable-http for a standalone HTTP backend.
    """
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
