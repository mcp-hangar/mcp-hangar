### a task's follow-ups follow its tool's current access

A relayed task's follow-ups are checked against the tool access in force when
they arrive, as a new call of the task's tool is: the tool policy, the
withdrawals and the caller's tenant. **Nothing needs changing in
configuration.** What a client may see when a tool is withdrawn, or the policy
stops allowing it for the caller's tenant, while its task runs:

- **`tasks/update` is refused** with `-32602`, the call's message, and
  `data.error_type` set to the call's refusal: `ToolWithdrawnError` or
  `ToolAccessDeniedError`. The upstream is not sent the input.
- **`tasks/get` still answers with the task's status.** A poll that would hand
  over what the tool produced, a result, an error or input requests, is refused
  the same way.
- **`tasks/cancel` is always served**, so a task whose tool was taken away can
  still be stopped.

A tool that is still allowed is followed up as before.
