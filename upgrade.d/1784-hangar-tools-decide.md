### `hangar_tools` lists what a call would be allowed, and raises no more for a group with nobody in rotation

`hangar_tools` and `hangar_details` now decide each name as `hangar_call` decides it, the way the front door's listings have since 2.34.0. Three things they used to show are gone from them, and were never callable:

- a tool of a group that one of its members denies or withdraws, for every tenant the load-balancer may route to that member. A tenant pinned to a member, or routed to it by a canary split, is decided on that member alone;
- a tool of a server named directly that a group owning it denies on that member, which the server-only decision used to miss;
- a tool withdrawn by `tool_projection` for the caller, which no management listing used to read.

`hangar_tools` on a group also stops doing two things from a listing: it no longer advances the group's load-balancer strategy, and it no longer starts a member. It reads the catalogue of a member already in rotation. A group with no member in rotation, which used to raise `no_healthy_members_in_group`, now answers `{"mcp_server": "<group>", "group": true, "tools": []}`; a group whose routable members are all dead answers the same with `"state": "dead"`. A client that treated that error as "warm the group first" should read the empty list the same way: `hangar_warm`, or a call through the group, brings a member into rotation.
