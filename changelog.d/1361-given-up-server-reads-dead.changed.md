**core:** a server Hangar gives up on now reads `dead` (4) on
`mcp_hangar_mcp_server_state`, and stays there until an explicit start or a
call. The recovery saga's give-up used to stop the server, which then read
`cold` (0), the state of a server nobody has called. A crashed process kept
reading `ready` and a start that failed below the degrade threshold kept
reading `initializing`, because neither published a state change. All three
now read `dead`. Health checks stay off while a server is dead, and a group
does not route a call to a dead member.

New gauge `mcp_hangar_mcp_server_last_healthy_timestamp_seconds`: when Hangar
last saw the server working (a passing health check, a completed start or a
successful tool call). It is kept when the server goes cold or dead.

An alert on `mcp_hangar_mcp_server_state == 0` no longer catches a server
Hangar gave up on. `UPGRADE.md` says what to use instead.
