### giving up on a server is recorded as a stop

When the recovery saga gives up on a server, the server now records
`McpServerStopped` with `reason: max_retries_exceeded`, and right after it the
`McpServerStateChanged` that moves it to `dead`. The server still ends `dead`,
live and when its stream is replayed.

- `mcp_hangar_mcp_server_stops_total{reason="max_retries_exceeded"}` counts
  each give-up once, as in 2.20.0. It is now counted from the event, the way
  `idle` and `shutdown` are. To alert on a give-up, use
  `increase(mcp_hangar_mcp_server_stops_total{reason="max_retries_exceeded"}[1h]) > 0`.
  A `reason!="idle"` rule also matches every operator stop.
- The `reason` label is a closed set, and the metric's HELP line lists it:
  `idle`, `shutdown`, `user_request`, `manual`, `failback`, `compensation`,
  `detection_enforcement:block` and `max_retries_exceeded`. A REST stop whose
  body names any other reason is now counted as `manual`. The response still
  returns the reason it was given.
- The audit log and the event store hold the new `McpServerStopped` record. A
  consumer that reads every `McpServerStopped` as `cold` should read the state
  change that follows it.
- The alert handler's warning for an unexpected stop now fires on a give-up.
- A failover whose backup is given up on ends, as it does when the backup is
  stopped. The primary's recovery no longer schedules a failback stop that
  would have turned the dead backup `cold`.
