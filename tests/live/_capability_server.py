"""An MCP server over HTTP whose one tool needs sampling, for the -32021 relay (#2315).

    python _capability_server.py <port>

``needs_sampling`` answers ``MissingRequiredClientCapabilityError`` (-32021)
naming ``{"sampling": {}}`` when the request did not declare sampling, as
2026-07-28 says a server must. The SDK sends that error on HTTP 400. Hangar
declares no sampling upstream, so every call through it gets the error.
"""

import sys

import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from mcp.shared.exceptions import MCPError

MISSING_REQUIRED_CLIENT_CAPABILITY = -32021

mcp = MCPServer("capability-provider")


@mcp.tool(name="needs_sampling")
def needs_sampling(ctx: Context) -> str:
    """Answer only a client that declared sampling."""
    capabilities = ctx.request_context.session.client_capabilities
    if capabilities is None or capabilities.sampling is None:
        raise MCPError(
            code=MISSING_REQUIRED_CLIENT_CAPABILITY,
            message="Client must declare the sampling capability",
            data={"requiredCapabilities": {"sampling": {}}},
        )
    return "sampled"


if __name__ == "__main__":
    port = int(sys.argv[1])
    app = mcp.streamable_http_app(host="127.0.0.1", stateless_http=True, json_response=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
