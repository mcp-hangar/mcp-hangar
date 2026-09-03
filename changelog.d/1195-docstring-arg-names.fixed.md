**core:** two docstrings documented an argument no function has. The CLI vocabulary rename
rewrote `mcp_server` to "MCP server" in every string the package renders, and an `Args:`
entry is not prose -- it is the parameter's name -- so `add._collect_config` and
`ConfigFileManager.add_mcp_server` ended up describing `MCP server:`. Nothing failed,
because nothing parses those at runtime, which is why a gate now checks that every
documented argument is a parameter of the function it documents.
