### deleting a configured server over the API is refused

`DELETE /api/mcp_servers/{id}` on a server that the configuration file declares now answers `409` with `"error": "declared_in_config"`. The server keeps running. Before, the delete stopped it and removed it from the pool, and the next reload or restart brought it back.

- To remove a configured server, delete it from the configuration and reload.
- To take it out of service for a while, stop it instead of deleting it.

Servers added with `POST /api/mcp_servers`, and servers that discovery registers, are deleted as before.

The Kubernetes operator sent this delete whenever an MCPServer CR was deleted, including a CR that mirrored a configured server. Operator versions that include mcp-hangar/mcp-hangar-operator#202 no longer do.
