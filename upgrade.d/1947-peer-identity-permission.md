### Deleting a key-pinned server needs `mcp_servers:peer_identity`

Deleting a server that declares `tls.peer_identity` now needs the `mcp_servers:peer_identity` permission, which only `admin` and `kubernetes-operator` hold. A custom role that deletes such servers needs the permission added. Under OPA authorization, the gateway asks for action `peer_identity` on resource type `mcp_servers`; a policy that does not know the action denies it, so allow it for the principals that manage key-pinned servers, including the operator's service account. Servers without a peer identity are unaffected.
