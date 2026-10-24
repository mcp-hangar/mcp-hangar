### Authoritative discovery sources now remove servers that disappear

A source configured with `mode: authoritative` now deregisters a server it has
stopped reporting once the server's TTL has passed since it was last seen:
90 s by default, or the `mcp.hangar.ttl` label on a container, the
`mcp-hangar.io/ttl` annotation on a pod, or `ttl` in a discovered file. Before this release such a server stayed registered until the next
restart, whatever the mode.

- If you rely on discovered servers staying registered after their container,
  pod or file is gone, set the source to `mode: additive`, which still never
  removes anything.
- A server that comes back within its TTL is left running. A source whose scan
  fails (daemon or API unreachable) removes nothing that cycle.
- Static servers from the configuration are never touched.
