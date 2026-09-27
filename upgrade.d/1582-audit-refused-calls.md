### Audit and compliance feeds now include refused tool calls

A tool call a gate, the `tool:invoke` check or an L7 policy refuses now produces
a `tool_invocation` audit record with `mcp.tool.status=denied`, where it
produced none before. The compliance exporters write it under a new event type,
`ToolInvocationDenied`: CEF signature `103` ("Tool Invocation Denied", severity
5), LEEF event id `103`, syslog MSGID `103` at warning, and
`"event_type": "ToolInvocationDenied"` in JSON lines. Nothing is removed or
renamed. A SIEM rule, parser or dashboard that counts every `tool_invocation`
record as a call that ran, or that keys on the known event types, should filter
on the status or add the new type. The refusal's reason is in the bounded fields
`gate` and `gateReason` (`gate_reason` in JSON lines), or `l7Verdict`, `l7Mode`,
`l7RuleKind` and `l7PolicyId` for an L7 refusal.
