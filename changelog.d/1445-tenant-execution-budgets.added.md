**core:** per-tenant execution budgets. The new, optional
`execution.tenant_limits` section gives each tenant a `max_concurrency`, an
`rps` and a `burst`, so one tenant can no longer take every execution slot. A
`"*"` entry gives each tenant that is not listed a budget of its own; without
one, an unlisted tenant and a caller with no tenant are refused. The budget is
taken after every policy gate, so a call that a policy refuses spends nothing.
The token is taken before an approval hold and a cold start, and given back if
the call is refused later. The slot is taken last, so a call held for approval
holds none. A call over its budget is refused at once with `TenantQuotaExceeded`,
which the front-door call log records as `denied` and
`mcp_hangar_tenant_quota_refusals_total` counts. A reload keeps the budget of
every tenant whose limits did not change. Budgets are counted per process. With
no `tenant_limits`, nothing changes.
