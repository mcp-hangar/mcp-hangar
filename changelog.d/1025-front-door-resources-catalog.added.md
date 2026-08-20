**core:** `front_door` now serves an upstream's resources, not just the
`resource_link`s it handed out. `resources/list` and `resources/templates/list`
aggregate live across the tenant's own projected upstreams (the same per-tenant
scoping as the prompts proxy), and `resources/read` reaches anything in that
catalogue — still through `relay_request` and still behind the fail-closed
`ui://` guard (SEP-1865).

Because a resource URI does not say which upstream owns it, and two upstreams
may legitimately serve the same one, **every URI the gateway hands out is now
namespaced** as `hangar://<upstream id>/<the upstream's own URI>` and translated
back on `resources/read`. The rewrite is unconditional, so a URI does not change
shape when an unrelated upstream appears, and it is applied wherever an upstream
payload crosses the front door: `resource_link` and embedded `resource` blocks
in tool results, prompt results and relayed task results, plus the `contents` of
a `resources/read` answer. Nothing is dropped on collision — two upstreams
serving `demo://doc/1` both stay listed, under distinct projected URIs. Clients
that captured a `resource_link` from 2.12.0 will see the new shape; the links
are per-replica and in-memory, so an upgrade re-issues them either way.
