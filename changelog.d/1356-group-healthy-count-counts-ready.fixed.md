**core:** a group's `healthy_count` counts the members that are `ready` and in
rotation. It counted every member in rotation that was not dead, `cold` ones
included, so a group could report `healthy_count: 3` with `is_available: false`
and its circuit open. Rotation size is now its own field,
`members_in_rotation_count`, on `GET /api/groups` and `GET /api/groups/{id}`,
`hangar_details`, `hangar_group_list`, `hangar_list`, `hangar_start`,
`hangar_group_rebalance`, `hangar_status`, `hangar_health` and
`hangar_metrics`. `hangar_status` and `hangar_health` report `healthy_members`
from the same count, so all of them agree about one group at one instant.

What the group decides is unchanged: `is_available`, the group state and the
`min_healthy` rule that closes an open circuit still count the members in
rotation that are not dead. A group whose members were reaped for being idle
still routes, and the next call through it starts a member, but it now reports
`healthy_count: 0` until one does. `UPGRADE.md` says what to read instead.
