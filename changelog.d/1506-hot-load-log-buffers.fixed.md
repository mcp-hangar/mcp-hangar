**core:** a server loaded with `hangar_load` now has a log buffer, and unloading
or deleting one releases it. A hot-loaded server was given none at all, so no
stderr reader was ever started for it and `GET /api/mcp_servers/{id}/logs`
served an empty list for a running server. It is attached before the process
starts, because the reader is spawned while the client is created and only when
a buffer is set by then. On the way out, the buffer registry is a process-wide
dict that removing a server does not reach, so an unloaded or deleted server's
output stayed registered under an id that is free again; both paths release it
now. A server that already holds a buffer keeps it, with the lines already in
it.
