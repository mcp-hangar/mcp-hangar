**core:** in `front_door`, a `tools/call` to a name that has left the caller's
tool list now answers `-32601` with `data: {"reason": "projection_changed"}`. A
client holding a stale list can then list again rather than retry or give up.
Only a name that the caller's own last `tools/list` on this replica served gets
the reason. Every other unknown name still gets the ordinary `-32601`, with no
`data`, byte for byte as before. That covers a name held by another tenant, a
name policy denies, and a name that exists nowhere. The reason is a constant:
it names no upstream, no tool and no count. What each caller was served is kept
per replica in a bounded LRU, keyed by tenant, principal and verified session.
A call that reaches another replica, or comes after eviction, gets the ordinary
error.
