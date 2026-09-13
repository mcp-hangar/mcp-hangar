**core:** a server Hangar gives up on now reads `dead` (4) on
`mcp_hangar_mcp_server_state`. The recovery saga's give-up used to stop the
server, which then read `cold` (0), the state of a server nobody has called. A
crashed process kept reading `ready` and `up` 1, and a start that failed below
the degrade threshold kept reading `initializing`, because neither published a
state change. All three now read `dead`, and `mcp_hangar_mcp_server_up` and
`mcp_hangar_mcp_server_initialized` change with them.

Why a server died decides what starts it again. A group never routes a call to
a server Hangar gave up on; it restarts a member whose process crashed, as it
always did. A call to any dead server waits out the server's backoff, and a
deliberate start does not. Health checks, the recovery saga, the GC and the
bulk warm-ups leave a dead server alone. A server that is deleted, unloaded or
reloaded away loses its lifecycle gauges.

New gauge `mcp_hangar_mcp_server_last_healthy_timestamp_seconds`: when Hangar
last saw the server working (a passing health check, a completed start or a
successful tool call). It is kept when the server goes cold or dead.

An alert on `mcp_hangar_mcp_server_state == 0` no longer catches a server
Hangar gave up on, and `sum(mcp_hangar_mcp_server_up) == 0` can newly fire.
`UPGRADE.md` says what to use instead.
