### Registering a subprocess or container server over REST needs `mcp_servers:local_launch`

`POST /api/mcp_servers/` with `mode` `subprocess`, `docker` or `container`, and
a `PUT`/`PATCH /api/mcp_servers/{id}` that sets `env` on such a server, now
need `mcp_servers:local_launch` as well as `mcp_servers:write`. Without it they
answer `403`. Registering a `remote` server, and every other field of an
update, still need only `mcp_servers:write`.

- `admin` and `provider-admin` hold the permission: nothing changes for them.
- `developer` does not. A developer key that registered local servers over REST
  gets `403` from now on. Servers it already registered are untouched: they
  still load at boot and start as before, because the check runs on the REST
  request, not when a stored server is read back.
- `kubernetes-operator` does not, and needs none: the operator registers only
  `remote` servers.
- With authentication off every caller may, as on every other route.

To let a principal keep registering local servers, assign it `provider-admin`,
or create a custom role that holds `mcp_servers:local_launch` with the
permissions it already had, through `POST /api/auth/roles`.
