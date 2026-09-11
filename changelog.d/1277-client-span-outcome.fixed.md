**core:** the CLIENT span of an upstream call now ends in ERROR, with a
bounded `error.type`, when the upstream answered with a failure. Over HTTP the
span closed once the request was sent, so an HTTP error status, a terminated
session, a response that was not JSON, and a JSON-RPC error in the body or in
an SSE event all left it UNSET, and so did a rejected notification. Over stdio,
a JSON-RPC error or a tool result with `isError: true` left it UNSET as well.
`error.type` takes the mcp SDK's server-side values: `http_<status>` for a
status failure (`http_404` for a terminated session), the JSON-RPC error code as
a string, `tool_error` for `isError: true`, and otherwise the exception's class
name, such as `JSONDecodeError` for a response that is not JSON or `ReadTimeout`
and `ConnectError` for a transport failure. No message or body text is recorded.
What a call returns or raises, and how it retries, is unchanged
