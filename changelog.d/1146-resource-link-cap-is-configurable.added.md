**core:** `resource_links.max_per_tenant` bounds how many handed-out
`resource_link` references the front door remembers for one tenant before
that tenant's oldest is forgotten. Absent, it stays at the previous constant of
4096, so nothing changes on upgrade. Raise it when
`mcp_hangar_resource_links_evicted_total{reason="tenant_cap"}` climbs for
tenants that legitimately hand out more. A value that is not a positive
integer refuses to start rather than falling back, and the section is known to
`validate_config`, so a typo is reported (and refused under
`HANGAR_CONFIG_STRICT=1`)
