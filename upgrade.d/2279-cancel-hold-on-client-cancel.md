### a held call the client gives up on is cancelled, not left for a late approval

When a stdio client cancels a `tools/call` that is held for approval -- its
own request timeout, or a `notifications/cancelled` -- the gateway now abandons the
hold, as it does when the batch is cancelled: the approval record moves from
`pending` to `cancelled` at once, with the reason "the held call was cancelled
before a decision; it did not run", and the call never runs. This holds for a
front door's flat call and for `hangar_call`. Before, only the request ended,
the record stayed `pending` until its window closed, and an approval given
meanwhile ran the call although the client had already been told it failed.

What changes for an existing deployment:

- An approval given after the client gave up is refused: `approvals approve`
  exits 1 and REST answers 409, where before they succeeded and the call ran.
  An approver who wants the action done must ask the agent to call again.
- The approval window no longer outlives the client's own request timeout,
  on a client that cancels the request when that timeout passes. The MCP
  Python SDK does, and the TypeScript SDK gives a request 60 s by default, so
  a held call can be cancelled long before `approval_timeout_seconds` ends.
  Raise the client's request timeout to leave the approver more time.
- A call approved before the client gave up is unchanged: it runs once, even
  if the client leaves while the upstream is still answering.
- Over streamable HTTP nothing changes yet: a client's cancel does not reach
  the held call, which stays open for its window (mcp-hangar#2289).
