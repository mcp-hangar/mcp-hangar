### The actor of an auth mutation is the caller; `*_by` body fields are refused

The auth REST routes used to accept the actor of a change in the request body:
`created_by` on `POST /api/auth/keys` and `POST /api/auth/roles`, `revoked_by` on
`DELETE /api/auth/keys/{key_id}` and `DELETE /api/auth/roles/revoke`,
`assigned_by` on `POST /api/auth/roles/assign`, and `updated_by` on
`PATCH /api/auth/roles/{role_name}`. Each was optional and defaulted to
`"system"`, and whatever it said was recorded in the domain event, the log line
and the response.

The actor is now always the principal that authenticated the request. A body
that still carries one of those fields -- whatever its value, including `null`
or the caller's own id -- is refused with `422` and a `ValidationError` naming
the field, and nothing is changed. `DELETE /api/auth/roles/{role_name}` records
the caller as `deleted_by` instead of `"system"`, and `POST /api/auth/keys`
returns `created_by` alongside the key.

Old:

```json
{"principal_id": "user:bob", "role_name": "developer", "assigned_by": "user:alice"}
```

New -- drop the field; the assigner is whoever the key or token belongs to:

```json
{"principal_id": "user:bob", "role_name": "developer"}
```

A script or client that sent these fields must stop sending them. With auth
disabled no principal is attached to a request, and the actor is recorded as
`anonymous`, never `system`; in practice the key and role routes have no store
to write to with auth off, so this only names the value should one be wired.
