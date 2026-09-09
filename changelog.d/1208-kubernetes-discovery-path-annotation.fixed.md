**core:** the `kubernetes` discovery source could register a server that
nothing could ever reach. It reports the pod's own address (so it clears the
SSRF check that refuses the `filesystem` source's private endpoints), but the
endpoint it built was always `http://<host>:<port>` with nothing after it --
a 404 against every server mounted the way the SDK's own reference server and
`mcp-proxy` both default to, and there was no annotation to say otherwise. A
new `mcp-hangar.io/path` pod annotation (default `/mcp`, matching that
convention) closes it; the address itself is still exactly what the API
server reported, so the SSRF property this source exists for is unchanged.
