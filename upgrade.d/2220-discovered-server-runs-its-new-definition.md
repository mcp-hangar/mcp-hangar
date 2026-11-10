### a discovered server that came back with a new definition runs that definition

A discovered server could come back under the same name with a different
definition after its deregistration had not applied. The gateway then kept the
old definition while it recorded the new one as running. This mostly affected a
standalone gateway on `persistence.backend: sqlite`, or with
`MCP_PERSISTENCE_ENABLED`. This release applies the new definition from now on.
It does not correct a server already recorded this way, because its record
reads as current.

On a gateway with a persistence backend, act once after upgrading:

- In `GET /api/mcp_servers`, find the servers whose `provenance` is
  `discovery`. A server whose name appears in an earlier
  `discovery_deregistration_fenced` log line is the likeliest to be affected.
- Delete each of them with `DELETE /api/mcp_servers/<id>` or the CLI. On its
  next cycle, discovery registers again every server a source still reports,
  from the current definition.

A gateway with no persistence backend keeps nothing across a restart, so it
needs no action.
