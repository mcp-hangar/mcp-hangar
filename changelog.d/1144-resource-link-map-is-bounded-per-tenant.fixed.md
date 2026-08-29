**core:** the handed-out `resource_link` map is now bounded per tenant, not
per process. One `OrderedDict` capped at 4096 links evicted oldest-first
across all tenants, so a tenant handing out 4096 links flushed every other
tenant's remembered links: they vanished from `resources/list` and, for an
upstream the tenant no longer projects, stopped resolving on `resources/read`.
Each tenant now has its own map with the same 4096-link cap, evicted only by
that tenant's own traffic, and the number of tenant maps is itself capped
(1024, least recently used evicted first) so minting identities cannot trade
one exhaustion for another. Restart and cross-replica survival are unchanged:
the map is still per-replica and in-memory
