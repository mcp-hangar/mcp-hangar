### `mcp.server.id` names the group on every span of a group call; the member is `hangar.route.backend`

`mcp_server.cold_start` and `command.send.InvokeToolCommand` used to carry the
selected group member in `mcp.server.id`, while every other span of the same
call carried the group. They now carry the group too, and the member moves to
`hangar.route.backend`.

A trace query, dashboard or alert that selects those spans by member, such as
`name = "command.send.InvokeToolCommand" AND mcp.server.id = "<member>"`, finds
nothing after the upgrade for calls routed through a group. Change it to
`hangar.route.backend = "<member>"`. A query by group keeps working and now
matches those spans as well. Calls to a server outside any group carry the same
value in both keys, so their queries need no change.

The lifecycle spans `mcp_server.launch` and `mcp_server.startup_wait` are not
opened by the call, and keep naming the member they start in `mcp.server.id`,
so a query over every span of a group call's trace still sees the member there.
