### each server stop is counted once

`mcp_hangar_mcp_server_stops_total` counted some stops twice. An idle reap added
2 under `reason="idle"`. A stop through `hangar_stop`, the REST stop or block,
or the failover saga added 1 under its own reason and 1 more under
`reason="shutdown"`. Each stop now adds 1, under the reason it was made for.

**Stop rates drop after the upgrade**, with no change in how often servers stop:

- `reason="idle"` halves.
- `reason="shutdown"` no longer counts a stop made through the stop command. It
  counts the stops Hangar makes by itself: a reload, an unload or delete, a
  group's `stop_all`, process exit.
- `user_request`, `manual`, `failback`, `compensation`,
  `detection_enforcement:block` and `max_retries_exceeded` count as before.
- A sum over every reason drops by one for each stop that was counted twice.

Review the alerts and recording rules on this counter, such as a
`rate(mcp_hangar_mcp_server_stops_total[5m])` threshold or a ratio against
`reason="shutdown"`.

**`McpServerStopped` carries the stop command's reason.** A stop through
`hangar_stop` is now recorded as `reason: user_request` instead of `shutdown`. A
REST stop is recorded under the reason its body names when the counter lists
that reason, and as `manual` otherwise; the response still repeats the reason
as given. A failback is recorded as `failback`, a compensation as
`compensation`, a block as `detection_enforcement:block`. The event store, the
audit log and the event stream show these values. The alert handler still
raises no alert for these stops, and the recovery saga still clears a server's
retry state after them.
