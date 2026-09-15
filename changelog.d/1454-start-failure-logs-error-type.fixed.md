**core:** a failed server start is logged by its error type, not by its
message. The message can carry what the upstream wrote or printed, and it
reached the logs on every path that starts a server: the front-door warm-up
(`front_door_warmup_failed`), the start itself (`mcp_server_start_failed`), a
group starting its members, the sagas that restart or fail over a server, and
the MCP handshake's `notifications/initialized` and session renegotiation. Each
of these lines now carries `error_type`, a bounded class name, in place of the
message. The caller still receives the full error from the start it asked for.

Four lines that were free text are now structured events, so a log query that
matched their old wording needs updating: `mcp_server_start_failed: <id>,
error=<text>` is `mcp_server_start_failed` with `mcp_server_id` and
`error_type`; `Failed to start member <id>: <text>` is
`group_member_start_failed`; `Member <id> degraded in group <group>: <reason>`
is `group_member_degraded` with `consecutive_failures`; and the saga manager's
`Saga ... failed: <text>` lines are `saga_step_failed`,
`saga_compensation_failed` and `saga_command_failed`.
