**security:** an auth mutation is recorded against the authenticated caller, not
whoever the request body names. `POST /api/auth/keys`, `DELETE /api/auth/keys/{key_id}`,
`POST /api/auth/roles/assign`, `DELETE /api/auth/roles/revoke`, `POST /api/auth/roles`,
`PATCH /api/auth/roles/{role_name}` and `DELETE /api/auth/roles/{role_name}` took
`created_by` / `revoked_by` / `assigned_by` / `updated_by` from the body, defaulting
to `"system"`, so an admin key could grant a role or mint a key and have the event,
the log line and the response attribute it to someone else. The actor is now the
caller's principal id (`anonymous` with auth disabled), a body that still carries
one of those fields is refused with 422, and `POST /api/auth/keys` echoes
`created_by` in its response.
