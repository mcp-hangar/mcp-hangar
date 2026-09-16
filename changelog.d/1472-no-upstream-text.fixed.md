**core:** three more paths no longer repeat an upstream's text. When an
upstream process refuses `initialize`, the log line is
`mcp_server_initialize_refused` with `exit_code` and `stderr_bytes`, the size of
the stderr it captured, in place of the `mcp_server_process_exit_code: <code>`
and `mcp_server_stderr: <text>` lines. `McpServerDegraded.reason` for a failed
start is the error's type, as `bounded_error_type` gives it, not the error's
text. A failed tool call sends the security handler its error type, not
`<type>: <message>`. The caller still gets the full error from the start or the
call it made.
