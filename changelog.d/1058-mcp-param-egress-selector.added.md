**core:** an `MCPEgressPolicy` can now select on SEP-2243 `Mcp-Param-*` headers
(`headers.allow` / `deny` / `requireApproval`, same glob precedence as the
tool-name rules), so region, tenant and priority are enforceable without
parsing the body. A request whose `MCP-Protocol-Version` predates mandatory
header-body validation never satisfies such a selector: nothing has checked
that its headers agree with its body, so the tool rules and the policy default
decide instead. Only `Mcp-Param-*` names are selectable -- a selector on
`Authorization` is refused at parse.
