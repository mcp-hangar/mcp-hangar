### a `hangar_*` tool call is charged to one rate-limit budget

`rate_limit` and `rate_limit.per_caller` keep their keys and their meaning.
**Nothing needs changing** in a configuration. What they admit changes in three
ways, each toward the configured numbers:

- **A server or group a call names no longer has a budget of its own.** The
  limited tools used to keep one per id, so starting four servers admitted four
  times the budget. Now a tool whose work is a command is charged at the command
  bus, once per command type: `hangar_start` and `hangar_stop` of a server,
  `hangar_tools`, `hangar_warm` and `hangar_reload_config`, whose budgets are
  `StartMcpServerCommand`, `StopMcpServerCommand` and
  `ReloadConfigurationCommand`. A tool whose work never reaches the bus is
  charged once to a budget named after it, whatever it names: `hangar_load`,
  `hangar_unload`, `hangar_approve`, `hangar_discover`, `hangar_sources`,
  `hangar_group_rebalance`, the continuation tools, and `hangar_start` and
  `hangar_stop` of a group.
- **A call is charged once.** The limited tools were charged by the deprecated
  `check_rate_limit()` and again by the command bus. A `hangar_start` of a server
  now spends one token, not two, and a refusal names the command, as in
  `... rate limit for StartMcpServerCommand is used up ...`. A call that names no
  server spends nothing, since it does no work.
- **Waiting no longer refills a bucket past its rate.** A bucket idle for the
  cleanup window (60 seconds) was dropped and came back full. It is now dropped
  only once it has refilled, so after a wait a caller gets what `rps` refilled in
  that time.

`mcp_hangar.server.validation.check_rate_limit()` is removed. It was deprecated
and nothing in Hangar calls it now. Code that called it can call
`charge_tool(<tool name>)` from the same module, which charges the caller's
budget and the shared one for that name.
