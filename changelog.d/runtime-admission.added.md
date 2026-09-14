Add opt-in per-tenant execution budgets, checked before execution slots and tool gates, with fail-closed admission for unconfigured tenants. Add MCP_REQUIRED_CATALOGUE to gate a replica until its configured servers have been discovered and reconcile failed starts without invoking tools. Bound HTTP graceful shutdown to 90 seconds.

Flat frontdoor listing and dispatch enforce per-tool caller RBAC. Preloaded and reloaded group members receive health recovery events.
