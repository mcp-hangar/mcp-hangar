**core:** a server added through `POST /api/mcp_servers` now gets a log buffer,
so `GET /api/mcp_servers/{id}/logs` returns its output. Servers added by a
reload or by hot-loading already got one; this was the third way a server joins.
Deleting it releases the buffer
