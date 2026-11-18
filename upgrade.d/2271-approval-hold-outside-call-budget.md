### an approval hold no longer counts against the call's budget

A call held for approval used to spend its batch budget while it waited. Once
the budget passed, the batch was cancelled: an approval that arrived after
that, even inside the approval window, was refused, recorded `cancelled`, and
the call read `CancellationError` without running. On a front door every flat
`tools/call` has a 30 s budget, so the effective approval window was 30 s
whatever `approval_timeout_seconds` and `approvals.stdio_wait_seconds` said.
The same cut applied to `hangar_call`'s `timeout` and to the Python facade's
`invoke(timeout_s=...)`.

The clock of a held call now stops for the hold. What decides the outcome is
the approval window alone: `approval_timeout_seconds`, and over stdio the
shorter of that and `approvals.stdio_wait_seconds`. An approval inside the
window dispatches the call, which then has the budget it had left when the hold
began. A call nobody answers is refused `approval_timeout` when the window
ends, as before.

What changes for an existing deployment:

- An approval given after the call budget but inside the approval window now
  runs the call. Before, it was refused and recorded `cancelled`. An approver
  who relied on "approve late" being a no-op must deny instead.
- A batch with a held call returns when the hold ends, as before, but the
  batch's deadline no longer cancels the batch. Only `fail_fast` and a stop
  that abandons in-flight batches still cancel a held call, and an approval
  after that is still refused and recorded `cancelled`.
- `invoke(timeout_s=...)` no longer raises `TimeoutError` while its call is
  held; it waits for the hold, then for at most the time that was left.
- `batch_cancellations_total{reason="timeout"}` still counts a batch whose
  deadline passed, but that deadline no longer cancels anything. Read it as
  "deadline passed", not "calls cancelled".

The client can still give up first. The gateway keeps the call held for the
whole approval window, but a client with its own request timeout stops waiting
when that timeout passes. The MCP TypeScript SDK gives a request 60 s by
default, which is why `approvals.stdio_wait_seconds` defaults to 50. Over HTTP
no wait limit applies. A client that disconnects does not cancel the held
call, so an approval given after the client has gone still runs the call,
with its side effects, and its result reaches nobody. Before this release the
30 s budget made that unreachable on a front door. Keep
`approval_timeout_seconds` below the request timeout of the clients you serve,
or raise that timeout in the client.
