### a tenant's server listings show fewer servers, and a tenant may not rebalance a group

A caller whose principal has a tenant is no longer shown, in `hangar_list`,
`hangar_status` or `hangar_health`, a group member its calls may not be routed
to, and the group counts it reads there and from `hangar_start` on a group are
over its reachable members only, with no `circuit_open`. `hangar_group_rebalance`
now refuses such a caller with `AccessDeniedError`, whatever its role. An
operator who reads the whole fleet, or rebalances a group, through a
tenant-bound key uses a key without a tenant instead.
