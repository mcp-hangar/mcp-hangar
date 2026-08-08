**core:** the SSRF check that guards a remote MCP server's endpoint is now
enforced at connect time, not only when the server is registered. httpx
re-resolved the hostname itself on every connection with no second check, so a
human-registered name that resolved to a public address at registration could be
re-pointed at an internal one -- `169.254.169.254`, `10.x`, `127.0.0.1` -- before
the next tool call (DNS rebinding). The client now re-applies the same policy on
every request and pins the connection to the validated IP, keeping the original
hostname for the `Host` header and TLS certificate verification. A
discovery-sourced endpoint may still be private, but only at an address the
container runtime reported for it.
