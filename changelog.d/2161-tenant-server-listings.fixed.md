**core:** the `mcp_servers` section of `hangar_list`, and `hangar_status` and
`hangar_health`, listed every configured server to every tenant, the group
members declared at the top level of the configuration included, and the group
counts they and `hangar_start` on a group reported were whole-group. A tenant
pinned to one member of a group was shown the other members' ids and states.
For a caller with a tenant, a server listing now shows the servers no group
owns plus the group members its calls may be routed to; each group's
`total_members`, `healthy_count` and `members_in_rotation_count` are taken over
those members, `circuit_open` is left out, and the summaries count what is
shown; the required catalogue `hangar_health` reports names none of the hidden
members either. `hangar_start` on a group reports the same counts to a tenant, with
`members_started` the number of its members ready in rotation; every member is
still started. `hangar_group_rebalance` acts on the whole group, so a caller
with a tenant is refused with `AccessDeniedError`. A caller without a tenant,
and every caller with auth off, gets the output it got before. Hiding a member
from a listing refuses nothing: a call that names a member directly is still
decided by tool access alone
