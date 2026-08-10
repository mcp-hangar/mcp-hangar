**core:** `MCP_TRUSTED_HOSTS` did not reach the MCP endpoint. The app was built
with the SDK's default transport security, which derives its allowlist from the
SDK's own bind host, so `/mcp` answered `421 Invalid Host header` to the
gateway's Service DNS name and to every Ingress host while the REST API on the
same process accepted them -- the two read different lists. Both serving paths
now build the guard from the configured allowlist, expanding each entry to match
with and without a port (the SDK compares the raw `Host` header, everything else
in Hangar strips it), with `*` opting out as it does elsewhere. Origins come from
the same `MCP_CORS_ORIGINS` list the WebSocket handshake already used
