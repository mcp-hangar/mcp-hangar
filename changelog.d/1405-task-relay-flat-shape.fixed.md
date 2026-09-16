**core:** an upstream task in the current flat shape (`resultType: "task"`) is
now relayed as one in the older nested shape is: it is recorded for its caller,
and `tasks/get`, `tasks/update` and `tasks/cancel` work for it, on `hangar_call`
and on the front door. Before, `hangar_call` handed the flat handle back
unrecorded, and the front door answered `-32602`. A task is now handed only to a
caller that can poll it: a 2026-07-28 client that declared the
`io.modelcontextprotocol/tasks` extension. Any other caller gets a tool error
naming the extension to declare (`TasksNotNegotiated` in a `hangar_call` batch),
not a task that `tasks/*` would refuse it. See `UPGRADE.md`.
