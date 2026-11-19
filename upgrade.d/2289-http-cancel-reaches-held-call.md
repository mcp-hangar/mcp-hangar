### a held call an HTTP client gave up on is cancelled

When a streamable HTTP client cancels a `tools/call` that is held for approval,
the approval now moves to `cancelled` and the call never runs, as it already
did over stdio. An approver who acts on it afterwards is refused ("was
cancelled: the held call will not run", REST 409).

The approval table gains two columns, `client_request_id` and `batch_id`. They
are added on start-up, on SQLite and PostgreSQL alike. Calls held before the
upgrade have neither, so a cancel does not reach them.

A cancel is matched by the caller's principal, tenant and JSON-RPC request id.
When the same principal and tenant have two held requests with the same id, for
example two agents sharing one API key, the cancel does nothing and both stay
held, as before. Give each agent its own credential if this matters.
