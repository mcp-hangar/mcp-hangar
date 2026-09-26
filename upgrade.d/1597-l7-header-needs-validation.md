### L7 header rules no longer match on `hangar_call`

An `MCPEgressPolicy` header rule (`headers.allow`, `headers.deny` or
`headers.requireApproval`) is no longer consulted for a call made through
`hangar_call`. That surface declares no `x-mcp-header`, so the SDK checks none of
its `Mcp-Param-*` headers against the body, and ADR-025 says a selector must not
match a header nothing checked. The call is decided by the policy's tool rules and
its `defaultAction`, and the verdict's reasons include "header rules not
consulted". A policy that relied on a header `allow` rule to let a `hangar_call`
through a tool-name default-deny now denies it.

The same holds on the front door for an `Mcp-Param-*` header the called tool does
not declare: only declared headers are matched.

To select on a header, call the tool on the front door (`tool_access.mode:
front_door`) with the tool declaring the header through `x-mcp-header` on the
argument it mirrors. That header is checked against the body before dispatch and
still matches. Otherwise, express the rule as a tool-name rule.
