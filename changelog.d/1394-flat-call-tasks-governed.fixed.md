**core:** a task an upstream creates in answer to a front door's flat
`tools/call` can now be polled. The flat call path never recorded who owned
the task, so `tasks/get`, `tasks/cancel` and `tasks/update` answered "Task not
found" to every caller, the one who created it included. With the SDK Hangar
ships, the call failed before that, with `-32602`, because the upstream's task
handle is not a tool result.

The flat call now records the task's owner exactly as `hangar_call` does: the
caller's tenant and principal, through the same code. It answers with the
SEP-2663 task result (`resultType: "task"`), and its fields are the ones
`tasks/get` serves. Any other caller's `tasks/*` on the task are refused as
they are on the `hangar_call` path, with the same response. If the task cannot
be recorded, the call fails the same way it does on `hangar_call`.
