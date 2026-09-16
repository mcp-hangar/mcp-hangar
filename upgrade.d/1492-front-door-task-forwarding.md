### a front door relays tasks from a current-spec upstream

A front door's flat `tools/call` now forwards the caller's
`io.modelcontextprotocol/tasks` declaration to the upstream, as `hangar_call`
already did. SEP-2663 leaves task creation to the upstream and gates it on the
caller having declared the extension, so an upstream that follows the spec used
to answer a front-door call with an ordinary tool result and never a task. Such
a call can now come back as a task result (`resultType: "task"`), which the
caller polls with `tasks/get`.

- **What changes for a caller.** A client that declares the extension under
  `clientCapabilities.extensions` on each request may now receive a task where
  it previously received a tool result, from upstreams that offer one. A client
  that declares nothing sees no change. Declaring the extension has always been
  the opt-in; on a front door it now has effect.
- **The same request context also carries two things it did not.** The caller's
  W3C trace context (`traceparent` / `tracestate` in `params._meta`) now parents
  the flat call's spans, so a front-door call joins the caller's trace instead of
  starting its own; and the request's `Mcp-Param-*` routing headers reach the L7
  egress evaluator on this path, so a policy selecting on them can now fire for a
  flat call.
- **A caller on an older revision is no longer spoken for.** Whether the caller
  declared the extension is read once per request, together with whether its
  protocol revision has `tasks/*` at all. A caller without `tasks/*` has no
  declaration forwarded on its behalf, since it could not poll the resulting
  task.

### a task no caller is handed is cancelled upstream

When an upstream answers a call with a task that Hangar will not hand over --
the caller cannot poll one -- Hangar now sends that upstream a best-effort
`tasks/cancel` for it. The caller still gets the same refusal it got before, and
it gets it without waiting: the cancel is sent off the request path, with one
attempt and a bounded timeout, and its outcome is logged (`cancelled`, `refused`
or `failed`) rather than surfaced.

Cancellation is cooperative under SEP-2663, so an upstream may decline. An
upstream that does honour it will see `tasks/cancel` arrive for a task it just
created, shortly after creating it, with no `tasks/get` in between. Before, that
task was left to run until its own TTL with nobody able to collect the result.
