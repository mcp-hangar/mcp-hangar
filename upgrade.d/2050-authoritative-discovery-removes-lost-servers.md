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

What counts as "no longer reported": a cycle in which the source answered and
reports itself healthy afterwards. A source that raises, or that cannot reach what
it watches -- Docker when the daemon does not answer, a filesystem path that is
missing -- removes nothing, however long that lasts. Two cases still count as a
real absence and remove the server once its TTL has run out:

- a Kubernetes namespace whose pod listing fails while the API itself answers;
- a discovery file that stops parsing.

A server another change now refuses to discover -- a Kubernetes local mode, a
Docker label volume outside `allowed_mount_paths`, a world-writable discovery
file -- is no longer reported either, so on an authoritative source it is removed
after its TTL rather than left registered.

