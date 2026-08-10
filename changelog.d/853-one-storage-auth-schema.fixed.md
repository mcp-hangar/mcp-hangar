**core:** an auth-enabled gateway could not start on a fresh database once
`persistence.backend` was set. The one-storage branch in `auth/bootstrap.py`
returns the backend's API-key, role and tool-access-policy stores as they are,
and those three keep schema creation in `initialize()` -- which nothing called,
on either backend. Startup reached the auth bootstrap and died on
`relation "roles" does not exist`, or, with no `role_assignments` configured to
trip it, on `tool_access_policies` a few lines later; SQLite failed the same way
with `no such table: roles`. Both backends now initialise those stores when they
build them, the way the event store already did. The legacy
`auth.storage.driver: postgresql` branch also stopped returning no tool-access
store at all, which is why naming the backend a second time was not a workaround
either
