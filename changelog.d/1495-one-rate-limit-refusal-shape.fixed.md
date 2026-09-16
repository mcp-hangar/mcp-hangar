**core:** a rate-limit refusal has one shape on every MCP tool path. A refusal
from the tool wrapper's own check left the wrapper as an MCP error, while one
raised inside a tool body -- by the command bus or by `charge_tool` -- came back
as a payload whose error key was `type`, so a client read three shapes for one
`RateLimitExceeded`. Every tool path now answers with the payload a
`hangar_call` result already uses: `error_type` names the failure, and `details`
carries `retry_after`, `scope`, `rps` and the budget's `limit` in the same
places. `error_type` replaces `type` in every tool error payload, not only a
refusal's. A refusal by the command bus's limiter now reaches the security
handler as a tool-level one does, once per refusal, with bounded fields only:
the scope, the kind of key, and the tool or command type it is named after. See
`UPGRADE.md`.
