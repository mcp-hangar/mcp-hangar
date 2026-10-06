### Approving a held call whose batch was cancelled is refused with 409

When a call is held for approval and its `hangar_call` batch deadline passes
before anyone answers, the call will not run. Before, approving it afterwards
answered `200` with `state: "approved"`, the record said `approved`, and
`ToolApprovalGranted` was published naming the approver in `decided_by`.

Now `POST /api/approvals/{id}/resolve` with `"decision": "approve"` answers
`409`:

```json
{"error": "Approval refused: the held call was cancelled and did not run", "state": "cancelled"}
```

The approval record moves to a new terminal state, `cancelled`
(`GET /api/approvals?state=cancelled` lists them), and the gate publishes
`ToolApprovalCancelled`, whose `attempted_by` names the approver, instead of
`ToolApprovalGranted`. The `mcp_hangar_approval_decisions` counter gains the
`decision="cancelled"` value. On a `cancelled` record, `decided_by` names who
tried to approve the call, not who let it through: nothing did.

A denial after the deadline is still recorded as a denial, and an unanswered
hold still expires. An approval tool or dashboard that treats every `409` from
resolve as "already resolved" should read `state`; an audit consumer that
enumerates approval events or states should add the new ones. An approval that
lands on a different gateway instance than the held call still answers `200`
there; the instance holding the call records it `cancelled` and publishes
`ToolApprovalCancelled`, not a grant.
