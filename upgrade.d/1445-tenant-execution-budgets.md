### per-tenant execution budgets

`execution.max_concurrency` bounds the whole process, so one tenant's burst
could take every execution slot. The new, optional `execution.tenant_limits`
section bounds each tenant on its own. **With no `tenant_limits` section,
nothing changes.**

```yaml
execution:
  max_concurrency: 50
  tenant_limits:
    "tenant:a": {max_concurrency: 4, rps: 10, burst: 20}
    "*": {max_concurrency: 2, rps: 5, burst: 10}
```

- `max_concurrency` is how many of the tenant's calls may be executing at
  once. `rps` and `burst` are a token bucket: calls start at `rps` per second
  on average, and at most `burst` at once. All three are required.
  `max_concurrency` and `burst` are integers from 1 to 1000000000, and `rps` is
  above 0 and at most 1000000. A misspelt or extra key, or a value out of
  range, refuses the configuration.
- **A tenant that is not listed** gets a budget of its own, built from the
  `"*"` entry. `"*"` is a template, not a pool that unlisted tenants share.
  Callers with no tenant share one such budget. **With authentication off, no
  caller has a tenant, so every caller shares that single `"*"` budget.**
- **With budgets configured and no `"*"` entry, an unlisted tenant and a
  caller with no tenant are refused.** If you add `tenant_limits` for a few
  tenants, add a `"*"` entry too, unless refusing everyone else is what you
  want.
- `none` cannot be a tenant id in `tenant_limits`: it is the metric's label for
  a call that no entry applied to.
- **Where the budget is taken.** Both parts come after the policy gates (tool
  access, withdrawal, pins, the circuit breaker and validators), so a call they
  refuse spends nothing.
  - The token, and the check that the caller has a budget at all, come before
    the approval hold and the cold start. A caller with no budget, or over its
    rate, is refused before anyone is asked to approve the call, and before the
    call starts a stopped server. On a server that has not started yet, a
    pinned tool's pin can only be checked once the server starts, so such a
    caller is refused with `TenantQuotaExceeded`, not a pin mismatch. A call
    refused after that point (denied or
    expired at approval, no longer valid after the hold, or its server failing
    to start) gets its token back.
  - The slot comes last, just before the upstream call. A call held for
    approval, or waiting on a cold start, holds no slot. The slot is given back
    when the call returns. A call that the upstream answers with a task handle
    returns with the handle, so a task the upstream keeps running does not hold
    a slot.
- **An approved call can still be refused** if all of its tenant's slots are
  taken when it is dispatched. The approval is then spent: running the call
  again needs a new one. The `tenant_quota_exceeded` log line, a warning in this
  case, names the approval. Leave room in `max_concurrency` for tools that need
  approval.
- A tenant at its concurrency limit can still start a stopped server, with a
  call that is then refused.
- A call over its budget is refused at once, never queued or retried, with the
  error type `TenantQuotaExceeded`. The front-door call log records it as
  `denied`, and `mcp_hangar_tenant_quota_refusals_total{budget,reason}` counts
  it, with `reason` one of `no_budget`, `concurrency` or `rate`.
- **Budgets are counted per process**, like `execution.max_concurrency`. With
  N replicas a tenant can run up to N times its budget, so size each budget for
  your replica count.
- A reload keeps the budget of every tenant whose limits did not change, with
  its calls in flight and its spent tokens. A tenant whose limits changed keeps
  counting the calls it has in flight, so lowering a limit never lets more than
  the new limit start. A tenant removed from `tenant_limits` is refused from
  then on, and calls it still has running are counted again if it is added
  back. Calls already running when budgets are first turned on are not
  counted.
