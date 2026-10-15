### Docker-discovered http servers default to the path `/mcp`

The endpoint of a container discovered in `http` or `sse` mode is now `http://host:port/mcp`; it was `http://host:port`. A container that serves MCP at the root, or elsewhere, must say so with the label `mcp.hangar.path` (for example `mcp.hangar.path: "/"`). Kubernetes discovery already behaved this way.
