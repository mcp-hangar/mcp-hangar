**core:** `hangar_whoami` tells a caller who the gateway thinks it is. It takes
no parameters and returns the caller's principal, tenant, groups and role
bindings with their scopes, read from the same lookup the authorizer decides on,
plus `opa_policy` when an OPA policy can change what those roles decide. It
needs no permission: on a `front_door` gateway every authenticated caller is
offered it on the management surface, including one with no role at all, and
so is a declared stdio principal. With auth off, or for an anonymous caller,
there is no identity to report, so it is absent there and refused if called (on
`egress`, where the whole `hangar_*` surface is listed, a call is refused).
