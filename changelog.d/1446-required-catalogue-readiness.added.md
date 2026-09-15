**core:** a front door can now wait for its catalogue before it reports ready.
The new `tool_access.required_catalogue` block lists the servers a `front_door`
replica must have projected before `/health/ready` answers 200, within a window
of `retry_for_s` seconds (600 by default) from when the configuration is first
applied, which has to cover the rest of boot and the warm-up. When the list is met, or the window ends, readiness goes back to
today's rule, so a replica is held out of the Service for at most `retry_for_s`.
The readiness endpoint is unauthenticated, so its `catalogue` field reports
counts and state. The missing ids, and why the retry will not start a server,
are logged in a `required_catalogue_waiting` line and returned by
`hangar_health`. Within the window, a server the boot warm-up could not start is
retried the way a call starts it, so a dead server waits out its backoff. The
retry never starts a server that is `dead` for `given_up` or
`capability_blocked`, leaves a `degraded` one to the recovery saga, never starts
one this replica has already projected, and ends in a final state. Each attempt
writes one log line, with the error type and not the error text, and one sample
of `mcp_hangar_catalogue_retries_total`. A group id is satisfied by any one of
its members. An unknown id, a group with no members, an unknown key, a
`retry_for_s` of 0 or less, and, with a `coordination:` block or shared storage,
a required server in a local mode refuse the configuration at load, from a file
and from a dict. Without the block, and in `egress`, readiness is unchanged.
A call's cold start in the batch executor now follows the call rules, in every
topology: a server that became capability-blocked, or went back into its
backoff, after the executor checked it is not started by the call, and is
refused as `CircuitBreakerOpen` or `CannotStartMcpServerError`, the codes the
executor's own check gives the same conditions.
