**core:** `provider-admin` could not deliver an egress policy. The route table
maps `/api/mcp_servers/{id}/l7_policy` to `policy:write` -- the permission the
role holds and the reason it exists -- but the two handlers ran a second,
in-handler check for `mcp_servers:write` on top, which `provider-admin` does not
hold and `developer` does. The operator's push answered 403 while the
`MCPEgressPolicy` CR still reported `Compiled` and `BackstopApplied`, so the
policy enforced its L3/L4 half and silently dropped its L7 half. Authorization
for both handlers now comes from the route table alone
