**core:** audit records now carry caller identity and tenant. The audit event
handler read identity keys that the identity context never produces, so no
OTLP audit record carried a caller id, a user, a session or a tenant, and the
CEF, LEEF, JSON-lines and syslog exporters never received a user or a session.
Audit records now carry `mcp.caller.id` (the user id, or the agent id when
there is no user), `mcp.user.id`, `mcp.session.id` when the identity has a
session, and the tenant as `mcp.caller.tenant_id`, beside `mcp.caller.type`.
The compliance exporters receive the user and the session. `mcp.caller.roles`
is still not emitted, because a call's identity carries no roles. A failed
call's `mcp.tool.duration_ms` is now the call's duration instead of 0.0. The
audit resource is now built as the trace resource is, so it carries the same
`service.instance.id`, `service.version` and `deployment.environment`, with
the same precedence. With no identity (auth off), records are unchanged
