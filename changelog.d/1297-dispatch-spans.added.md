**core:** a command or query dispatch now leaves a span naming what was
dispatched and how it ended. `CommandBus.send` opened `handler.{Command}` only
inside its innermost step, so a command the rate-limit middleware refused never
reached it: the trace held one `rate_limit.check` span with `allowed=false` and
nothing saying which command had been refused. `QueryBus.execute` opened no span
at all, so every management read -- a fleet listing, an invocation history --
was a gap between the request arriving and the response leaving.

Both buses now open `dispatch.{Operation}` carrying
`hangar.dispatch.operation` (the class name, never a payload) and
`hangar.dispatch.outcome`: `success`, `rejected` or `error`. A rate limit is
`rejected`, not an error -- middleware answered the question it exists to
answer, and an operator counting failures should not be counting refusals.

The span covers middleware AND handler, so `rate_limit.check` and `handler.*`
are now its children, with their own names and attributes unchanged. It nests
under whatever span is already active and never starts a trace of its own.

This covers every entry point at once: REST routes, the MCP management tools
that call the buses directly, and the batch executor.
