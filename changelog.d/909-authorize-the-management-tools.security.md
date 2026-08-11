**core:** twenty-one of the twenty-two `hangar_*` tools authorized nothing.
`hangar_call` checked `tool:invoke` for every call it dispatched; `hangar_start`,
`hangar_stop`, `hangar_load`, `hangar_unload`, `hangar_reload_config`,
`hangar_quarantine`, `hangar_approve` and the rest mutated the fleet on the
say-so of anyone who got past authentication. The same operations over REST have
been permission-gated since 2.2.0, so with auth on, one identity in one process
was refused `POST /api/mcp_servers/{id}/stop` and accepted on `hangar_stop`.

Four places could have enforced it and none did: the MCP endpoint's ASGI wrapper
authenticates and never authorizes, no server middleware is installed, the
shared tool decorator did rate limiting and validation only, and the tool bodies
dispatch straight to the command bus.

Authorization is now resolved from the tool name against a declarative table, the
same inversion the REST route table made and for the same reason -- a tool absent
from the table is refused rather than public. Each entry mirrors what the REST
route performing the same operation already requires, so no role changes: reads
take `mcp_servers:read`, lifecycle takes `mcp_servers:lifecycle`, load and unload
take `mcp_servers:write`, reload takes `config:reload`, the discovery tools split
into `discovery:read` / `trigger` / `approve`. Auth off remains allow-all, as it
already was on the `hangar_call` path, so a `--unsafe-no-auth` gateway is
unchanged.

**A principal that could drive these tools over MCP without holding the matching
permission will now be refused.** If an API key was working through `hangar_*`
because MCP asked for nothing, it needs the role its REST equivalent has always
needed
