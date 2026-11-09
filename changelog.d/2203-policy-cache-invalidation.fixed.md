**core:** a server-scope tool access policy set or removed at runtime did not
reach a caller whose effective policy was already cached. A provider policy on
`_global`, which every scope reads, and one on a group id, which every call
through that group reads, dropped only the cache entries that named that id,
so `POST /api/auth/policies/provider/<id>` left such a caller resolving against
the previous policy until a reload or restart. Removing a server's policies on
unload or reload missed the group entries of a member with no member policy of
its own, and removing a member policy missed a member that had a mapping but no
policy. Every writer and remover of a server's own or per-tenant policies now
invalidates through one rule that covers every scope reading the changed
policy. Merge order and semantics are unchanged
