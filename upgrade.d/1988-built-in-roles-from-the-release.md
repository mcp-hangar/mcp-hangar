### Built-in roles are enforced as the running release defines them

A gateway now ignores the stored permissions of a built-in role (`admin`, `provider-admin`, `kubernetes-operator`, `developer`, `viewer`, `auditor`, `service-account`) and enforces its own release's definition. The `roles` table in a SQLite or PostgreSQL auth store keeps its rows for older replicas, which still read them. If you edited a built-in role's row in the database to change what it grants, that edit no longer has an effect. Create a custom role with the permissions you want and assign it instead.

`POST /api/auth/roles` with a built-in role's name is now refused with 403 (`CannotModifyBuiltinRoleError`). It used to overwrite the built-in row.
