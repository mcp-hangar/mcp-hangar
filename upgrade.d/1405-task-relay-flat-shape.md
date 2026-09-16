### an upstream task is handed only to a caller that can poll it

When an upstream answers a `tools/call` with a task, Hangar now hands the task
only to a caller that can poll it: a client on the 2026-07-28 revision that
declared the `io.modelcontextprotocol/tasks` extension. That is the caller
`tasks/get`, `tasks/update` and `tasks/cancel` already served.

- **Any other caller gets a tool error, not a task.** On the front door's flat
  `tools/call` it is a result with `isError: true` whose text names the
  extension to declare. In a `hangar_call` batch the call fails with
  `error_type: "TasksNotNegotiated"`. Before, such a caller was handed the task,
  and `tasks/*` then refused it (`-32021` or `-32601`), so it could never read
  the task.
- **The upstream has still made the task.** Hangar does not record it and does
  not cancel it.
- **An upstream task in the current flat shape now works.** A task an upstream
  answers with as `resultType: "task"` (SEP-2663) is recorded and relayed like
  one in the older nested shape. On `hangar_call` the flat handle used to come
  back unrecorded, so `tasks/*` answered "Task not found", and the front door
  answered `-32602`.

To keep receiving tasks, declare `io.modelcontextprotocol/tasks` under
`clientCapabilities.extensions` on each request.
