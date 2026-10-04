**core:** a running gateway now shows each tool's computed digest. Every tool in
`hangar_tools`, `GET /api/tools` and `GET /api/mcp_servers/{id}/tools` carries
`digest`, the SHA-256 schema fingerprint `mcp-hangar pin` computes, and
`pinned_digest` when a pin covers it, so a pin mismatch can be read without the
CLI. `pinned_digest` is omitted when there is no pin. On `hangar_tools` it is
the pin for the caller's tenant, or the all-tenants one; on the REST routes,
which admit only a fleet-wide grant, it is the all-tenants pin. The digest is
shown only where the tool itself is: a tool a caller cannot list has no digest
or pin in its answer. On by default, with no setting: a digest fingerprints a
schema the caller can already read.
