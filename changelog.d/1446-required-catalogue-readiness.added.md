**core:** a front door can now wait for its catalogue before it reports ready.
The new `tool_access.required_catalogue` block lists the servers a `front_door`
replica must have projected before `/health/ready` answers 200. Until then it
answers 503, and a `catalogue` field names what is missing, which servers the
retry will not start and why, and the retry's state. A server the boot warm-up
could not start is retried for up to `retry_for_s` seconds (600 by default, 0
turns the retry off). The retry starts a server the way a call does, so a dead
server waits out its backoff. It never starts a server that is `dead` for
`given_up` or `capability_blocked`, leaves a `degraded` one to the recovery
saga, and never starts one this replica has already projected, so an
idle-stopped server stays stopped. Each attempt writes one log line, with the
error type and not the error text, and one sample of
`mcp_hangar_catalogue_retries_total`. Once every listed server has been
projected, readiness no longer depends on backends. A group id is satisfied by
any one of its members. An unknown id, a group with no members, or an unknown
key refuses the configuration at load, from a file and from a dict. Without the
block, and in `egress`, readiness is unchanged.
