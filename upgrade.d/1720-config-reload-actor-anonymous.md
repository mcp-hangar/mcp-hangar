### Config reload with auth off is recorded as `anonymous`

`POST /api/config/reload` with authentication disabled recorded `requested_by="api"`; it now records `anonymous`, the same value the auth routes use. If you filter the audit trail or logs on `requested_by="api"`, match `anonymous` as well. With auth on, the caller's principal id is recorded as before.
