**core:** the front door's per-call log line now says what the caller got. A
flat `tools/call` whose upstream answer the gateway could not turn into a
`CallToolResult` -- one with no `content` -- was validated by the SDK *after*
the line was written, so the line said `outcome: ok` for a call the client
received as a JSON-RPC `-32602`. The result the caller receives is now built
inside the logged scope, so the line and the client agree: such a call is
answered with a tool error (`isError`) and logged `outcome: tool_error`,
`reason: invalid_result`. The upstream's payload is in neither the line nor the
answer. See `UPGRADE.md`.
