### Withdrawing or restoring a tool for every tenant needs the administrator's grant

`POST /api/admin/tools/{server}/{tool}/withdraw` and `.../restore` with no `tenant_id` act on every tenant, and now answer `403` to a global `developer`; the `admin` role (or any grant holding `admin:*`) is required. Send the `tenant_id` to act on one tenant, which a global developer and a tenant-scoped developer can still do. If a script or an operator relied on a developer key for a fleet-wide withdrawal, give that key an administrator role or name the tenants.
