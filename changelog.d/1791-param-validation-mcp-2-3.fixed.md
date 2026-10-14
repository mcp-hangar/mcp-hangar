**security:** `Mcp-Param-*` headers of a call to a front-door tool are compared
with its body under mcp 2.3.0, as they are under 2.0.0. 2.3.0 asks
`Server.get_tool_input_schema` for the schema instead of running `tools/list`,
and that hook only knows the SDK's own tools, so the check was skipped for every
projected upstream tool and a call an L7 header rule denies reached its upstream.
Hangar now answers the hook from its projection, and runs the pre-dispatch
listing itself where the SDK no longer does. Nothing changes on 2.0.0, the pinned
version; this lets the pin move
