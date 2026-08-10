**core:** `mcp-hangar auth bootstrap-admin` refused to run on a deployment that
had made the one storage decision. It consulted only `auth.storage.driver`,
which defaults to `memory`, and answered "driver 'memory' is not durable" -- on
exactly the deployments where it is the only way in, since `/api/auth/**`
requires an admin principal with no carve-out for the first call. It now uses
the backend `persistence.backend` selected, which is durable by construction.
The claim it makes also stopped colliding with a configured `auth.role_assignments`
entry for the same principal: the admin assignment is inserted with the same
conflict tolerance `assign_role` has always had, on both backends
