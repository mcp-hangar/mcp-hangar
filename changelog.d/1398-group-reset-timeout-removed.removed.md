**core:** a group's `circuit_breaker.reset_timeout_s` is removed. It never had
an effect: an open group circuit did not half-open once the timeout passed,
because a group never asks its breaker whether to let a request through. The
circuit still closes once `min_healthy` members are back in rotation. A config
that still sets the key loads and logs a warning naming the group;
`HANGAR_CONFIG_STRICT=1` and `mcp-hangar config check` refuse it.
`McpServerGroup` no longer accepts `circuit_reset_timeout_s`. See `UPGRADE.md`
