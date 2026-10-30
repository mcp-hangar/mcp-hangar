**core:** `GET /api/system` now requires `mcp_servers:read` held globally, like
the other routes that report on the whole fleet. `GET /api/system/me` is
unchanged. A key that reads `/api/system` needs a role that grants this, such as
the built-in `viewer`, bound at global scope.
