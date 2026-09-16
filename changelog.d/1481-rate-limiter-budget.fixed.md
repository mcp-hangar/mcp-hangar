**core:** the command-bus rate limit admits what it is configured to. A bucket
idle for the cleanup window was dropped even if it had not refilled, so the next
call found a full one. `hangar_start`, `hangar_stop`, `hangar_tools`,
`hangar_approve`, `hangar_load`, `hangar_unload` and `hangar_group_rebalance`
kept a budget per server or group a call named, so naming more of them bought
more calls. The limited `hangar_*` tools were also charged by the deprecated
`check_rate_limit()` as well as by the command bus: two budgets for one call.

A bucket is now dropped only once it has refilled. A tool whose work is a
command, such as `hangar_start` of a server, is charged once, at the command
bus. A tool whose work never reaches the bus, such as `hangar_load` or a group
start, is charged once to a budget named after the tool, whatever it names.
`check_rate_limit()` is removed. See `UPGRADE.md`.
