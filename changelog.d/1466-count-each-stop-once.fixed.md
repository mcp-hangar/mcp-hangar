**core:** each server stop is counted once in `mcp_hangar_mcp_server_stops_total`.
An idle reap was counted twice under `idle`. A stop through the stop command
(`hangar_stop`, the REST stop or block, a failback) was counted under its own
reason and again under `shutdown`. Each stop is now counted once, from its
`McpServerStopped` event, which carries the stop command's reason instead of
`shutdown`. Stop rates drop after the upgrade with no change in how often
servers stop, so review the alerts and recording rules on this counter. See
UPGRADE.md.
