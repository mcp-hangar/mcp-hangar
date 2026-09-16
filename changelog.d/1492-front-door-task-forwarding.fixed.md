**core:** a front door now forwards the caller's tasks declaration upstream, so
a current-spec upstream creates tasks there. The flat `tools/call` ran the
executor without the request context, so the caller's
`io.modelcontextprotocol/tasks` declaration was never read and never relayed. An
upstream that follows SEP-2663 -- which creates a task only for a client that
declared the extension -- therefore never created one for a front-door caller,
and only upstreams that create tasks unasked ever produced one. The same request
context also carries the caller's W3C trace context and its `Mcp-Param-*`
routing headers into the call, as it already did on `hangar_call`.

A task no caller is handed is now cancelled upstream. When an upstream creates a
task for a caller that cannot poll one, Hangar refuses the task, as before, and
additionally sends that upstream a best-effort `tasks/cancel` for it, off the
request path and bounded, so work nobody can collect no longer runs on to its
own TTL. The outcome is logged by type; no upstream text is logged.

Whether a caller declared the extension is now read once per request and used
both to decide what is forwarded upstream and whether a task may be handed back,
so the two cannot disagree. A caller on a revision without `tasks/*` no longer
has a declaration forwarded on its behalf.
