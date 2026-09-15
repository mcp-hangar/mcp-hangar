**core:** giving up on a server is recorded as a stop with its own reason.
When the recovery saga gives up on a server, the server records
`McpServerStopped` with `reason: max_retries_exceeded`, then the move to `dead`,
so the audit log, the event store and the alert handler see the give-up as a
stop. `mcp_hangar_mcp_server_stops_total{reason="max_retries_exceeded"}` still
counts each give-up once, now from that event, as `idle` and `shutdown` are
counted. The `reason` label is a closed set, listed in the metric's HELP line:
a REST stop that names any other reason is counted as `manual`. A failover
whose backup is given up on ends, so no failback stop turns the dead backup
`cold`. See UPGRADE.md.
