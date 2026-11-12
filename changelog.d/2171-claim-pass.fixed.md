**core:** a server the configuration file took over from the API, and later
dropped, no longer comes back as a registered server after a restart; one
taken over from discovery comes back only if its source still reports it.
The takeover wrote nothing, so the row stayed a
registration. The manager now claims the row of every server its file
declares: a standalone gateway at boot, before anything is served, and after
each reload; on PostgreSQL only the lease holder, when its tenure begins,
after its own reloads and every minute, each write fenced by the lease
generation. A claim never deletes a row: a replica whose file still declares
the server keeps reading its L7 policy from it. `GET /api/system` reports the
claimed rows this replica's file no longer declares as
`instance.dormant_file_claimed_rows`
