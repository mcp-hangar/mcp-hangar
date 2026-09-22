**core:** tool-call spans now carry the caller's identity. `batch.call.<tool>`
-- the one enrichment boundary both `hangar_call` and a front-door flat call
pass through (ADR-029) -- gets `mcp.caller.type`, `mcp.caller.id`,
`mcp.caller.tenant_id`, `mcp.user.id`, `mcp.agent.id`, `mcp.session.id` and
`mcp.correlation_id`, read from the bound identity context and from nothing
else. Unknown values are omitted rather than exported empty, so a query on
`mcp.caller.tenant_id` selects the calls that actually had a tenant.

Baggage is deliberately not a source: anything that forwards a request can
write it, and a tenant label taken from there would be a claim the gateway
never authenticated.

`TracedMcpServerService` is removed with this change. It was the only caller of
`set_governance_attributes` and nothing in `src/` ever constructed it, so the
governance attributes it describes were never emitted on a real call path. See
the upgrade note.
