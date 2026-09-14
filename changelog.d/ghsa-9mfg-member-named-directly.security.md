**security:** a `hangar_call` that names a group member by its server id is now
governed by the member's group as well (GHSA-9mfg-mfwg-7cvr). Before this fix,
naming the member instead of the group ran the call as if the member were a
standalone server. The group's `tools:` policy, the per-member `tools:` in the
group spec, and any withdrawal or digest pin declared on the group were not
applied. A tool the operator denied, withdrew or pinned on the group ran when a
caller named a member. The `egress` mode, the default, was affected with auth
off, and with auth on for any principal holding `tool:invoke`. `front_door` was
not affected.

A call that names a member is still sent to that member and never through the
group's member selection. For each group that owns the member, it is now
checked the way a call naming that group and routed to that member is checked:

- the group's policy, the member's policy in the group spec, and the caller's
  tenant policy;
- a withdrawal on the group, for every tenant or for the caller's tenant;
- a digest pin on the group, enforced in the group's `digest_enforcement` mode.

The member's own server-level policy, withdrawals and pins still apply too, as
they did before. The re-check after an approval hold asks the same question. A
server that is a member of several groups is governed by all of them, and deny
wins: a tool any one of them denies, withdraws or pins to another digest is
refused. A call that names a group is unchanged, and so is a server that is in
no group.

The same advisory covers two more gaps in how `hangar_call` scopes governance.

Approval lists are now read with the same scope as the access policy. Before,
the approval gate read only the `approval_list` of the server a call named, and
asked without the caller's tenant. The following never held a call:

- a group's `approval_list`, whether declared on the group or on a member in
  the group spec;
- a tenant's `approval_list` under `tool_access.member.<tenant>`;
- in `front_door`, any approval list at all. Asked without a tenant, the
  resolver gives the no-identity answer, which requires no approval.

A tool on any approval list that applies to the call now needs approval. The
first list that applies supplies the timeout and channel. Approval routed by an
L7 egress policy's `requireApproval` is unchanged.

The re-check after an approval hold now includes withdrawal. A tool withdrawn
while a call waited for approval used to run once the call was approved. That
covers a withdrawal on the server, on a group that owns it, for every tenant or
for the caller's tenant, by config reload or at runtime. The call is now refused
with `ToolWithdrawnError`, as it would have been before the hold.
