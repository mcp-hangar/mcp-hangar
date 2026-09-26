**core:** a caller that waits for a cold server's start on the aggregate's
readiness event, rather than in single flight, now links its
`mcp_server.startup_wait` span to the start it waits on, as single flight's
waiters already did. That is most followers of a concurrent burst, and until
now their waits carried no link. The link targets the leader's
`mcp_server.cold_start` when the leader came through the batch executor, else
the span the start runs in; a wait on a start with no known trace context has
no link, and a later start never links to an earlier one. No attribute changes.
