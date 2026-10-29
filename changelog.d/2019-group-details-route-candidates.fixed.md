**core:** `hangar_details` on a group disclosed every member to every tenant. A
tenant pinned to one member of a group was shown the others too, though its
calls only ever went to that one. For a caller with a tenant, `members` now
lists only the members its calls may be routed to (the pinned or canary member
alone, else every member in rotation), the set `hangar_tools` on a group
already decides on, and `total_members`, `healthy_count`,
`members_in_rotation_count`, `state` and `is_available` are taken over those
members alone. A tenant in rotation is therefore no longer shown members out
of rotation, and a group with no selectable member shows an empty member
list. A caller without a tenant (auth off) is shown the whole group, as before
