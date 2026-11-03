### Authorization, L7 and rate-limit refusals have a new first line

A `tool:invoke` denial, an L7 refusal, an upstream key mismatch at dispatch and a command-bus rate limit now start with `Refused by MCP Hangar (<layer>): <reason>. <next step>`. The text they had moved to the second line of `error` and of the tool result's text, so a client matching `Not authorized to invoke tool`, `Tool call denied by egress policy` or `RateLimitExceeded:` from the start of the text has to read `_meta["io.mcp-hangar/toolFailure"]` instead. `error_type` is unchanged.

A rate-limited call now writes a `ToolCallRefused` to the event store and one `denied` audit record, so traffic refused at the limit costs a write. The record's `hangar.gate.name` is `rate_limit`.
