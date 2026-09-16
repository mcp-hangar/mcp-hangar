### a call naming a group waits for the approver its member's L7 policy asks for

A call that names a group is routed to one of the group's members. When that
member's L7 egress policy (`MCPEgressPolicy`) is in `Enforce` mode and routes
the tool to `requireApproval`, the call is now held and an approval is raised,
exactly as for a call that names the member directly.

Before, the approval gate looked the L7 policy up by the id the call named. A
group id is not a server id, so a call naming a group found no policy and
nobody was asked; the member's own check then refused the call at invoke with
`EgressPolicyApprovalRequiredError`.

**A call that was refused immediately may now wait.** With an approval gate
configured, such a call blocks until someone decides it, or until the timeout:

- approved -- the call runs, and its result comes back as usual;
- denied -- it is refused with `approval_denied`;
- nobody answers -- it is refused with `approval_timeout` after
  `approval_timeout_seconds`, which is 300 seconds when the rule comes from
  the L7 policy alone and no tool-access policy narrows it.

If a caller of yours relied on the immediate refusal, give it a timeout, or
change the rule from `requireApproval` to `deny` where you meant "refuse"
rather than "ask". Check as well that the configured channel reaches a person
(`approvals.channel`): on `noop` the hold is real and nobody is told, so the
call runs out its timeout.

**Nothing changes without an approval gate.** With approvals disabled, or with
no gate wired, the call is still refused at invoke with
`EgressPolicyApprovalRequiredError`, as before. The member's own invoke-time
L7 check is untouched and still refuses first, so `deny` still wins and
`Audit` mode still asks nobody. A call that names the member directly behaves
as it did.
