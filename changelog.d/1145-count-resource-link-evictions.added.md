**core:** `mcp_hangar_resource_links_evicted_total` counts handed-out
`resource_link`s the front door forgot, by `reason`: `tenant_cap` (a tenant's
oldest link at its own 4096-link cap) or `tenant_map_cap` (every link of a
tenant dropped by the tenant-map LRU). Until now an eviction left no record and
the victim's `resources/list` simply got shorter. Not labelled by tenant, for
the same cardinality reason as `mcp_hangar_empty_projection_total`
