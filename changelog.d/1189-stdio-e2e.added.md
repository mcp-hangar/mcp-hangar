**core:** the 2.18.0 exit gate now runs on every pull request, not only at release. A new
integration test drives `mcp-hangar serve` over stdio with the SDK's own `ClientSession`
and asserts the three claims the release is built on: with `auth.stdio.principal`,
`front_door` serves the upstream's flat tool names and one of them can be called; without
it, the same configuration serves zero tools (the fail-closed behaviour from #902, which
ADR-026 must not loosen); and a tool whose digest does not match its pin is refused.

Every one of those already had an in-process test, and none of them would have caught what
this covers -- identity binding depends on which task reads the contextvar, projection
depends on a real `initialize` handshake, and the refusal depends on the flat dispatcher
reaching the executor rather than `hangar_call`.
