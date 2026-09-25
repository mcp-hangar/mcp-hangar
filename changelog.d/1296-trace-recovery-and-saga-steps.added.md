**core:** background recovery is now traced as bounded operations. Each server
the health worker actually checks gets one `mcp_server.health_check` span with
`hangar.health.outcome` (`healthy`, `unhealthy`, `error`) and
`mcp.health.consecutive_failures`; a recovery command the saga sends on that
check's events is its child. A command a saga schedules on a timer fires as
`saga.scheduled_command` in a new trace with one link to the span that
scheduled it, and no link when that cause is unknown. Each `start_saga` run is
one `saga.run` span with `hangar.saga.type`, `hangar.saga.outcome` and one
`hangar.saga.step` event per step (`completed`, `no_action`, `failed`,
`compensated`, `compensation_failed`). A discovery follower that skips cycles
because another instance holds the lease records a `discovery.lease_transition`
span when it becomes a follower and when it takes the lease back, never once
per skipped cycle and never as a `discovery.cycle`. Skipped servers and idle
ticks emit nothing, and saga, timer and lease behaviour is unchanged (#1296).
