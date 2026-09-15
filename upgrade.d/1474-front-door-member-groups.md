### a member of several groups is governed by each of them on the front door

On the front door, a server that is a member of several groups is now governed
by every one of those groups, as `hangar_call` naming that server already was.
Before, the front door kept one group per member, whichever group the file
declared last. The other groups' `tools` policy, `tool_projection` withdrawals
and pins, and `header_exposure` block did not apply to that member there.
**A server in one group, or in none, is unaffected.** For a server in several
groups:

- **It may now be refused a tool it was served before.** A tool that any of its
  groups denies or withdraws, for every tenant or for the caller's, is no longer
  listed for it, and a call to it is refused. A call to a tool any of its groups
  pins is checked against that pin. This is the decision `hangar_call` already
  gave for the same config.
- **A call is routed to the server itself**, not through one of its groups, so
  no group's `strategy` picks the member that answers. A server in one group is
  still routed through its group.
- **Its tools are counted under its own id** in
  `mcp_hangar_projected_upstream_bytes`, not under a group's.

To serve such a tool again, allow it in every group the server is a member of,
or take the server out of the group that refuses it.
