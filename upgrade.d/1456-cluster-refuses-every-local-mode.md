### a cluster refuses mode-less servers and local inline group members

A configuration with a `coordination:` block is refused at startup when it
declares a server that runs as a child process of one gateway, because only the
replica holding the management lease can run it. The check read only top-level
`mcp_servers` entries whose `mode` was written as `subprocess`, `docker` or
`container`. It now also refuses:

- a server with no `mode`. The loader builds it as `subprocess`, with or
  without an `endpoint`;
- a group member whose id names no server under `mcp_servers`, when its own
  entry in the group's `members:` list defines a server with a local or missing
  `mode`. The error names it `<group>/<member>`. An entry that says neither is
  still refused by the loader as naming no server;
- `mode: podman`. That configuration already failed to load, later, with
  `'podman' is not a valid McpServerMode`.

To fix a refused configuration, give each server it names `mode: remote` and an
`endpoint`. For a group member, do that in the member's entry, or declare the
server under `mcp_servers` and name it by its id. If this is one gateway,
remove the `coordination:` block. A configuration without `coordination:` is
unaffected.
