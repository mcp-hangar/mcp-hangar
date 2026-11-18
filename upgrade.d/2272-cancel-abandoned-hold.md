### an abandoned approval hold reads cancelled from the moment it is abandoned

When the caller of a held call gives up on it -- its batch deadline passes, a
`fail_fast` sibling fails, or a bounded HTTP stop abandons it -- the gateway now
ends the hold there and records the approval `cancelled` at once. Before, the
hold went on to the end of its approval window and the record stayed `pending`
meanwhile, so a resolver in another process could approve a call that would
not run.

Two outcomes read differently:

- A hold nobody answered before it was abandoned reads `cancelled`, not
  `expired`, with the reason "the held call was cancelled before a decision; it
  did not run". The call's result is `CancellationError`, not
  `approval_timeout`, and the batch returns when the hold is abandoned rather
  than when its approval window ends. `ToolApprovalCancelled` is published for
  it with an empty `attempted_by`, and no `ToolApprovalExpired`.
- A denial that arrives after the abandonment is refused like an approval: the
  record stays `cancelled`, `approvals deny` exits 1 and REST answers 409. Only
  a denial that reached the record before the abandonment reads `denied`.

Anything that counted abandoned holds as `expired`, or read `approval_timeout`
or `approval_denied` for a call whose batch was cancelled, should count the
`cancelled` state and `CancellationError` instead.
