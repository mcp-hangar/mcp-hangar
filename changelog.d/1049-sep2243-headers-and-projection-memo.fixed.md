**core:** a handshake-era `Mcp-Name` that used the SEP-2243 base64 sentinel was
refused as a header/body mismatch, and a modern `tools/call` with arguments
recounted `mcp_hangar_projected_tools` because the SDK's schema lookup re-ran
`tools/list`. Routing headers now go through `decode_header_value`, a mismatch
answers `-32020` (`HEADER_MISMATCH`) like `tasks/*`, and the identity-scoped
projection is memoised for the lifetime of one HTTP request so the metric
still measures listings a client actually received.
