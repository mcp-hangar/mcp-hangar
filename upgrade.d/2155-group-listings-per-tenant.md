### a caller with a tenant sees only its own view of a group

`hangar_details`, `hangar_list`, `hangar_group_list`, `GET /api/groups` and
`GET /api/groups/{id}` used to show every caller the whole group. A caller whose
principal has a `tenant_id` now gets a view cut to the members its calls may be
routed to:

- `members` holds only those members, so a pinned tenant sees one member, and a
  tenant in rotation does not see members that are out of rotation.
- `total_members`, `healthy_count`, `members_in_rotation_count`, `state` and
  `is_available` are taken over those members.
- `min_healthy` and `circuit_open` are absent.
- `hangar_list(state_filter=...)` filters on the `state` of that view.
- `GET /api/groups/{id}` answers 200 with an empty `members` list when the
  tenant has no routable member, not 404.

A caller without a tenant, and every caller with auth off, gets the same output
as before.

To move across: a script or dashboard that reads whole-group figures through a
key bound to a tenant, or that reads `min_healthy` or `circuit_open` from these
outputs, should use a principal without a tenant. Code that reads those two keys
for a tenant has to handle their absence.
