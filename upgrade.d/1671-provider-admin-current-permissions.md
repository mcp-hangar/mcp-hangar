### `provider-admin` can now register, start, stop and remove servers

The built-in `provider-admin` role held the pre-rename `provider:*` permissions, which no route or tool checks, so it could read servers and push egress policy but nothing else. It now holds `mcp_servers:write` and `mcp_servers:lifecycle`.

Any API key or role assignment on `provider-admin` gains those four capabilities: `POST /api/mcp_servers`, `PUT` and `DELETE /api/mcp_servers/{id}`, and `start` and `stop`. It still has no `config:reload`, no `admin:*` and no access to the credential store. If a key must stay narrower, move it to `viewer`, or to `kubernetes-operator` for the Kubernetes operator.
