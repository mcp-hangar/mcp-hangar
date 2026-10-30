**core:** `hangar_list`, `hangar_group_list`, `GET /api/groups` and
`GET /api/groups/{id}` still disclosed every member of a group to every
tenant, after `hangar_details` stopped doing so. A tenant pinned to one member
was shown the others too, with their ids, states and counts. For a caller with
a tenant, each of them now gives the view `hangar_details` gives: only the
members its calls may be routed to, every count, `state` and `is_available`
taken over those members, and no `min_healthy` or `circuit_open`.
`hangar_list(state_filter=...)` filters on that view's `state`. On the REST
routes the tenant is the authenticated principal's own `tenant_id`, the one
the MCP tools read; a group the tenant has no routable member of is an empty
member list, not a 404. A caller without a tenant, and every caller with auth
off, is shown the whole group as before, so an operator who needs the whole
group over REST uses a principal without a tenant
