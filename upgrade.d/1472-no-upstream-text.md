### a failed start's degraded event and initialize log carry no upstream text

- When an upstream process answers `initialize` with an error, Hangar logs one
  `mcp_server_initialize_refused` line with `mcp_server_id`, `exit_code` and
  `stderr_bytes`. The `mcp_server_process_exit_code: <code>` and
  `mcp_server_stderr: <text>` lines are gone. The stderr still reaches the
  caller, in the `McpServerStartError` the start raises. A log query or alert
  that matched the old lines needs updating.
- `McpServerDegraded.reason` for a failed start is the error's type, such as
  `McpServerStartError` or `ConnectionError`, where it was the error's text.
  The event store, the audit log, the alert handler's `details.reason` and the
  security handler's `details.reason` carry the new value. A server that health
  checks degraded still reads `health_check_failures`. Events recorded by an
  earlier release keep the text they were written with.
- A failed tool call's security record (`log_validation_failed`, field `tool`)
  has the error's type as its message, where it was `<type>: <message>`.
