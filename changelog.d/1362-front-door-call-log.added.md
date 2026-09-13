**core:** every call on the front door's flat projected surface now writes one
structured INFO line, `front_door_tool_call`, whatever its outcome. The line
carries `tool`, `outcome` (`ok`, `tool_error`, `denied`, `not_found`, `rejected`
or `error`), `reason`, `principal_id`, `tenant_id`, `request_id` (the JSON-RPC
id) and `duration_ms`, plus `trace_id` and `span_id` when tracing is on. A denied
call is logged with its verdict. That includes a suspended session, and the
`-32601` that a policy deny becomes on this surface, which the log marks
`not_projected`. The caller still gets the same `-32601` as for a name that does
not exist. No arguments are logged.
