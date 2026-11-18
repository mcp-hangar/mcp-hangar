**core:** an approval of a held call the gateway had already abandoned was
accepted by a resolver in another process. When a batch was cancelled while a
call was held for approval, only the gateway's memory knew, so
`mcp-hangar approvals approve` (or REST on another replica) recorded
`approved`, printed `approved by ...` and exited 0 for a call that never ran;
the record turned `cancelled` only when the approval window ended. The gateway
now moves the record from `pending` to `cancelled` by compare-and-swap the
moment it abandons the hold, so `approvals list` stops showing it as pending
and every resolver is refused: the CLI exits 1 with "was cancelled: the held
call will not run", and REST answers 409. An approval that reaches the record
just before the cancel is still recorded `cancelled`, never granted
