### Changing a server's endpoint over the API needs durable storage

`PUT /mcp_servers/{id}` with `endpoint` or `tls` now works for a server without a peer identity too. Every replica, including the one that handled the request, replaces its copy of the server from the shared configuration row. A gateway without durable storage (`persistence.backend` unset) has no shared row, and no projection to apply the change, so it refuses the change with 422 instead of keeping the old connection open.
