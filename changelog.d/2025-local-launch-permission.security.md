**security:** registering a server the gateway launches on its own host now needs
`mcp_servers:local_launch`. A `subprocess`, `docker` or `container` server runs
a command or image the caller chooses, and its `env` is read when that process
starts, so `POST /api/mcp_servers/` for any mode but `remote`, and a
`PUT`/`PATCH` that sets `env` on such a server, used to let any holder of
`mcp_servers:write` -- the built-in `developer` role included -- run code on the
gateway host. `admin` and `provider-admin` hold the new permission; `developer`,
`kubernetes-operator` and the read-only roles do not. Registering a `remote`
server is unchanged
