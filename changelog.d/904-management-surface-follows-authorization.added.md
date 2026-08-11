**core:** a `front_door` gateway now serves each caller the management tools it
is authorized to call, instead of serving none to anybody. The mode swapped the
whole surface at bootstrap -- flat upstream names for everyone, `hangar_*` for
nobody -- so an operator on a front door had no control plane over MCP at all,
and turning the mode off to get one handed every agent the entire meta-API.
Satisfying both meant running two instances.

The decision is the one the invoke path already makes: a management tool appears
in `tools/list` exactly when the caller may call it, resolved from the same
`TOOL_PERMISSIONS` table and the same authorizer. So the list cannot drift from
the enforcement, a tool that was shown is callable, and a tool that was not is
still `-32601` if a client guesses the name. The surface is as narrow as the
caller's role: a principal that may invoke tools and administer nothing sees only
upstream tools, an operator holding `mcp_servers:read` reads the fleet, and
neither gets anything it could not already do over REST.

Stricter than the invoke path in one respect, deliberately: with auth off the
management surface is empty rather than complete. `--unsafe-no-auth` allows every
invoke for backward compatibility, but projecting on that rule would hand an
unauthenticated front-door caller a control plane it does not have today.

`egress` is unchanged and still serves every caller the whole meta-API -- there
it *is* the surface, and a client with no `hangar_call` can reach no upstream
tool at all.

Also adds `mcp_hangar_projected_tools`, a histogram of how many tools a
front-door `tools/list` returned, split by `kind=governed|management`. The
surface sits in an agent's prompt prefix and is paid for on every turn, and
nothing on the server side could see how large it was
