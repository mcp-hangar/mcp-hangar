### a front-door call the gateway cannot answer is a tool error, not `-32602`

On a front door, a flat `tools/call` whose upstream answer could not be turned
into a valid `CallToolResult` -- one with no `content` -- reached the client as
a JSON-RPC error with code `-32602` ("Invalid params"), which blamed the
caller's arguments for an upstream's answer. The gateway's own per-call log line
said `outcome: ok` for that same call, so the log and the client disagreed
about it.

Such a call is now answered the way every other failure on this surface is: a
result with `isError: true`, whose text says the upstream result could not be
returned. The upstream's payload is not quoted in it, and a valid result is
unaffected.

- A client that watched for the `-32602` now sees an ordinary tool error, and
  has to read `isError` to notice the failure.
- The `front_door_tool_call` line for that call now reads
  `outcome: tool_error` with `reason: invalid_result`. An alert that counts
  non-`ok` outcomes will see calls it used to count as `ok` -- they were
  already failing for the client.
