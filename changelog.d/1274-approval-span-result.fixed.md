**core:** the `approval_gate.check` span's `approval.result` now says
`approved` when a human granted the hold and the dispatch re-check confirmed
it, and `unavailable` when an L7 egress policy requires approval for the tool
but no approval gate is configured, a call that is still refused at dispatch.
Both used to read `not_required`, so a human-approved call looked like one that
never needed approval. `not_required` now means no approval was asked for, and
the refusal values (`approval_denied`, `approval_timeout`, `ApprovalGateError`,
`ApprovalDenied`, `revalidation_failed`) are unchanged. Which calls are allowed
or refused is unchanged
