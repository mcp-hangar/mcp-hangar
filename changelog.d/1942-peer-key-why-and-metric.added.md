**core:** a refused upstream key (ADR-030) is visible in three more places.
- `mcp-hangar why` lists a cold-start refusal and a warm call refused for its upstream's key. For each it prints the key the upstream presented and the key list the configuration file declares now. A warm refusal is read from its `ToolInvocationFailed` with `failure_code`, and is never re-labelled as a `ToolCallRefused`.
- The new metric `mcp_hangar_upstream_peer_key_connections_total{mcp_server, outcome}` counts every TLS handshake with a key-pinned upstream, including the GET stream's, as `verified` or `refused`.
- The upstream call span carries `hangar.upstream.peer_spki` for the connection that served it.
