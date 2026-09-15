**core:** a tool call made while a configuration reload swaps the servers and
groups now reads group membership with the governance overlays, as one set.
The reload swapped the servers and each group's membership after the
tool-access policies, withdrawals, pins and `header_exposure` blocks. A call to
a group member named directly in between was governed by the groups that owned
it before the reload, under their new policies: a member moved from a group
that denies `t` to one that allows it, while the group it left withdraws `t`
instead, was refused as withdrawn, which neither file says. The servers, the
groups and their membership are now swapped in the same set as the overlays,
and `hangar_call`, a task's follow-ups, the re-check after an approval hold and
the front door read which groups own a member in the same decision as the
policies. The L7 egress policy the approval gate routes a call on is read in
that decision too.
