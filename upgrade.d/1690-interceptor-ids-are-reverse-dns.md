### The negotiated `interceptors/list` shape returns the reverse-DNS interceptor ids

With the `MCP-Interceptor-Ext: io.modelcontextprotocol/interceptors` header, `interceptors/list` now answers `io.mcp-hangar.validator` and `io.mcp-hangar.mutator`, as the default shape already did, instead of `mcp-hangar-validator` and `mcp-hangar-mutator`. A client that keyed on the old names reads the new ones. `interceptor/invoke` still accepts the old names but its answer, and the `InterceptorInvoked` event, carry the new id.
