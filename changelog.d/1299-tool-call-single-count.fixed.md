**core:** every tool call through `hangar_call` or a projected tool was
counted twice: `InvokeToolHandler` observed it, and `MetricsEventHandler`
observed it again from the call's `ToolInvocationCompleted` or
`ToolInvocationFailed` event. `mcp_hangar_tool_calls_total`,
`mcp_hangar_tool_call_errors_total` and the count of
`mcp_hangar_tool_call_duration_seconds` now move by one per call, so every
call-volume series halves after upgrading. Review any threshold tuned against
the doubled series, such as the call-volume alert in the Helm charts. A failed
call now carries one `error_type`: the event's value (`OSError`, a JSON-RPC
code, `tool_error`) when the upstream was reached, the exception class
(`ToolNotFoundError`, `EgressPolicyDeniedError`) when it was not. The
`ToolInvocationError` series that duplicated upstream failures stops growing.
The latency histogram now holds only the upstream round trip; the dropped
second observation also timed policy checks and cold starts
