**core:** the gateway introduced itself to every upstream MCP server as
`mcp-registry / 1.0.0` -- a product name that has not existed for a long time,
at a literal version that never moved while the gateway sending it was 2.5.2.
It now sends `mcp-hangar` and the running package version, read from package
metadata so the two cannot drift apart again.

This is what an upstream operator sees in their logs when working out who is
calling them, and it is not only the handshake: the same identity rides
`params._meta["io.modelcontextprotocol/clientInfo"]` on every request to a
modern upstream. Nothing needs to change on your side -- but if you match on
`mcp-registry` in upstream log filters, alerting or client-specific
workarounds, those match on `mcp-hangar` from this release.
