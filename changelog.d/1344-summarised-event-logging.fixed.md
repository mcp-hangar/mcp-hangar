**core:** the `domain_event` log line no longer carries the whole event.
`LoggingEventHandler` wrote `event.to_dict()` into every line, so at the
default INFO level the structured log held each event's `identity_context`
and its free-text fields, such as error messages and reasons.
`ToolInvocationFailed`, `McpServerDegraded` and `HealthCheckFailed` did so at
WARNING. The `domain_event` line now carries only `event_type`, `event_id`,
`mcp_server_id`, `tool_name`, `error_type`, `tenant_id` and `correlation_id`,
each only when the event has it, at the same level as before. The full event
is logged in a separate `domain_event_detail` line at DEBUG, built only when
DEBUG is enabled. Consumers that parse event payloads from INFO or WARNING
lines must enable DEBUG or read the event store. The event store, the audit
trail and the security log are unchanged
