**core:** a `tools/call` whose `Mcp-Param-*` headers were never checked left no
metric. Hangar now increments `mcp_hangar_param_header_validation_skipped_total`
when the nested listing fails, omits the tool, or advertises an invalid
`x-mcp-header`, and when a handshake-era call still carries `Mcp-Param-*`.
The fail-open boundary itself is unchanged.
