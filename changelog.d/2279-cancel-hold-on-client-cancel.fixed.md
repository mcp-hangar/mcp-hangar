**core:** a stdio client that gave up on a call held for approval left the
approval open. When the client cancelled the `tools/call` (its own request timeout, or
`notifications/cancelled`), only the request ended: the call stayed held, the
record stayed `pending`, and a later `mcp-hangar approvals approve` (or REST)
exited 0 and ran a call the client had been told failed. The cancel now reaches
the hold, on a front door's flat call and on `hangar_call` alike: the record
moves to `cancelled` at once, every resolver is refused ("was cancelled: the
held call will not run", REST 409), and the call never runs. A call a human
granted before the client left still runs, once. Over streamable HTTP the
client's cancel does not reach the hold yet (mcp-hangar#2289)
