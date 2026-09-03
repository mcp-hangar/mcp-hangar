**core:** two identifiers came back. The CLI vocabulary rename rewrote `mcp_server`
wherever the package renders it, and reached inside a code span: `McpServer` (the class)
and `mcp_servers` (the configuration section) became `MCP server` and `MCP servers` in
`build_mcp_server`'s docstring, so a reader following either one finds nothing -- a class
that does not exist, and a section nobody can write. A gate now refuses a code span in the
CLI package that holds a phrase with a space in it, which is what an identifier never is.
