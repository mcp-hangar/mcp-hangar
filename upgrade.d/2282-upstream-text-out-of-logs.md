### an upstream's raw output is logged only at DEBUG

`stdio_client_process_stderr` no longer carries the process's stderr at INFO or ERROR, only its size in `stderr_bytes`. The text is in a new `stdio_client_process_stderr_text` line at DEBUG. Likewise, `stdio_client_malformed_json` carries `length` and `error_type` in place of `preview` and `error`, and `http_client_sse_invalid_json` carries `length` in place of `data`. Their text is in `stdio_client_malformed_json_text` and `http_client_sse_invalid_json_text` at DEBUG.

A log search or an alert that read a crashed server's stderr from `stdio_client_process_stderr` needs `logging.level: DEBUG` for that server's gateway. Alternatively, it can read the server's log buffer (`GET /api/mcp_servers/{id}/logs`), which still records a subprocess server's stderr line by line while it runs.
