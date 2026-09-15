**core:** a configuration reload no longer restarts servers whose settings did
not change. The reload diff compared part of each server's entry with the
running server, and counted a server whose entry left `resources` out as
changed on every reload. That is most servers: each reload stopped them and
dropped their in-flight calls. A server is now kept, with its process,
sessions, health and circuit state, unless the file changes something the
server is built from, with every default applied. A change to its tool-access
policy, `access`, `tool_access`, `tool_projection` or `header_exposure` block,
or to its `weight` or `priority` in a group, now takes effect without a
restart. `mcp_servers_updated` lists only the servers the reload restarted. See
`UPGRADE.md`.
