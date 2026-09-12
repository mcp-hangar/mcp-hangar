**security:** tenant-scoped role grants are now limited to their tenant
(GHSA-48jg-9vqq-gmqv). A role bound at `tenant:<id>` used to authorize REST
routes, the `/ws/events` stream and the `hangar_*` management tools as if it
were bound globally.

A tenant-scoped grant now passes only the routes that confine what they serve
to the caller's tenant, and each of those serves or changes only that tenant:

- `/ws/events` delivers only events that name the tenant. Events that name no
  tenant are withheld.
- `GET /mcp_servers/{id}/tools/history` returns only that tenant's invocations.
- `POST /admin/tools/{server}/{tool}/withdraw|restore` acts on that tenant only.
  An omitted `tenant_id` means that tenant, never every tenant, and lifting a
  withdrawal that covers every tenant needs a global grant.
- the approvals routes list, show and resolve only approvals that name the
  tenant. Approvals that name no tenant are withheld.

Every other route answers 403 (close code 1008 on a websocket), and every
`hangar_*` management tool is refused and left out of the front-door listing.
Global grants and deployments with auth disabled are unchanged. If you gave a
principal fleet-wide access through a `tenant:` binding, rebind that role at
`global` scope.
