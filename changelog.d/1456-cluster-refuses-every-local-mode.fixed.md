**core:** a `coordination:` block now refuses every server the loader builds as
a child process of one gateway. The check read only top-level `mcp_servers`
entries whose `mode` was written as `subprocess`, `docker` or `container`, so
three shapes passed it:

- a server with no `mode`, which the loader builds as `subprocess`;
- a group member defined in the group's `members:` list whose `mode` is local
  or missing, now reported as `<group>/<member>`;
- `mode: podman`, which then failed later, when the server was built, with
  `'podman' is not a valid McpServerMode`.

The refused modes are now the launcher's `LOCAL_MODES`, the set it refuses to
start on a replica that does not hold the lease. Group members are read in the
order the loader builds them. A member is the top-level server of that id only
when that server is listed before the group. A server built once is reported
once.
