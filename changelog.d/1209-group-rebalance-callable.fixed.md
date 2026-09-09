**core:** `hangar_group_rebalance` was uncallable since it was introduced --
the tool's own parameter is `group`, but the validator wired via
`mcp_tool_wrapper` was `validate_mcp_server_id_input`, whose parameter is
`mcp_server`. MCP delivers tool arguments by name, so no call could satisfy
both. Now wired to a dedicated `validate_group_id_input`.
