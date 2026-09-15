**core:** a `coordination:` block now refuses every server the loader builds as
a child process of one gateway. The check read only top-level `mcp_servers`
entries whose `mode` was written as `subprocess`, `docker` or `container`, so
three shapes passed it:

- a server with no `mode`, which the loader builds as `subprocess`;
- a group member whose id names no server under `mcp_servers`, built from its
  own `members:` entry with a local or missing `mode`, now reported as
  `<group>/<member>`;
- `mode: podman`, which then failed later, when the server was built, with
  `'podman' is not a valid McpServerMode`.

The refused modes are now the launcher's `LOCAL_MODES`, the set it refuses to
start on a replica that does not hold the lease. A member whose id names a
top-level server is that server, wherever the group is in the file, and is
checked once, as that server.
