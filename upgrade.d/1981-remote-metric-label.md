### Remote servers' HTTP metrics are labelled with the server id

The `mcp_server` label of a remote server's HTTP client metrics now holds the server's id, the name in `mcp_servers`, instead of its endpoint's host. This covers `mcp_hangar_upstream_peer_key_connections_total`, `mcp_hangar_http_errors_total` and the other `mcp_hangar_http_*` series. A dashboard or alert that selected these series by host, for example `mcp_server="10.244.3.17"`, selects nothing after the upgrade. Select by the server id instead. The old series stop growing, and new ones start under the id.
