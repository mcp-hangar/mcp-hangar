### Discovery no longer removes a server re-pointed or re-created over the API

A discovered server that is re-pointed over the API, or deleted and created
again over the API under the same name, is an operator's server from then on.
Discovery used to delete it anyway once its source stopped reporting the name
and the TTL ran out. It now leaves it in place, logs
`discovery_deregistration_refused_not_discovered` at INFO once, and stops
tracking the name.

If you relied on discovery cleaning such a server up when its container, pod
or file went away, delete it yourself with `DELETE /api/mcp_servers/{id}`. Servers discovery
registered and nobody changed are removed exactly as before.
