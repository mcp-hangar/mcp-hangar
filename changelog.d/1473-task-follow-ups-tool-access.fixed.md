**core:** a relayed task's follow-ups get the answer a new call of its tool
gets. When a new call of the task's tool would be refused by the tool policy or
a withdrawal for the caller's tenant, `tasks/update` is refused with `-32602`,
the call's message and `data.error_type` (`ToolAccessDeniedError` or
`ToolWithdrawnError`), and the upstream is not sent the input. `tasks/get`
still answers with the task's status, and refuses a poll that would hand over
the tool's result, error or input requests. `tasks/cancel` is always served. A
tool that is still allowed is followed up as before. See UPGRADE.md.
