**core:** a trace can now tell the caller starting a server from the callers
waiting for it. Ten concurrent calls to one cold server produce one start and
nine waits, and until now every one of them got the same `mcp_server.cold_start`
span: the nine watching looked exactly like the one working.

Each waiting caller gets a `mcp_server.startup_wait` span carrying
`hangar.startup.role=waiter` and `hangar.startup.mechanism`, **linked** to the
start it waited for rather than parented under it -- many waiters share one
cause, and a shared cause is a link (ADR-029 s2). The caller performing the
start is marked `hangar.startup.role=leader` on the span it already has.

Both waiting mechanisms report: single flight, which deduplicates callers that
arrive while the server is still cold, and the aggregate's own `ensure_ready`,
which catches the callers that arrive after the leader moved it to
INITIALIZING and so never reach single flight at all. That second wait had no
span of any kind before.

The aggregate reports through a narrow domain port with a silent default, so
`domain/` still imports no tracer and a deployment with tracing off pays a
context-manager entry and nothing else.
