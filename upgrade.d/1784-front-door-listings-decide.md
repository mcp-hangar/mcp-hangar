### A front-door listing shows only what a call would be allowed

`tools/list`, `prompts/list`, `resources/list`, `resources/templates/list` and `server/discover` now decide each name on the member a call would be routed to, exactly as the call decides it. Three things a listing used to show are gone from it, and were never callable:

- a tool, prompt or resource of a group that one of its members denies or withdraws, for every tenant the load-balancer may route to that member. A tenant pinned to a member, or routed to it by a canary split, is decided on that member alone;
- a prompt or resource of a group that a member-level `access` policy denies, which the group-level decision used to miss;
- everything of a group that has no member in rotation. A call to such a group is refused with `NoAvailableMemberError`, and the listing now agrees with it.

A client that caches a listing will see these names disappear after the upgrade. To list a name again, allow it on every member the tenant may be routed to, or pin the tenant to the member that allows it. A listing no longer advances a group's load-balancer strategy or starts a cold member; `prompts/list` and `resources/list` of a group whose members are all cold contribute nothing until a call starts one.
