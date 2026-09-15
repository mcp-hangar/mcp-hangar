**core:** `hangar_details`, `hangar_list`, `hangar_status`, `GET /api/mcp_servers`
and `GET /api/mcp_servers/{id}` now say why a server is `dead`. Each server
entry has a new `dead` field, null unless the state is `dead`. Then it holds
`reason`, one of `given_up`, `crashed`, `start_failed` and `capability_blocked`
(`unknown` only for a server restored from a record written before the reason
was kept); `since`, when the server went dead; `retry_allowed_at`, the latest
time its backoff ends, or null when no call starts it or no backoff applies;
and `revived_by`, `call_or_start` or `start`. The times are ISO 8601 UTC. The
`hangar_status` note for a dead server now names the reason and what starts it
again. The reason is a fixed value, never the upstream's error text.
