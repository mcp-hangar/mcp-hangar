### the Python facade's `invoke` applies the configured controls

`Hangar.invoke` and `SyncHangar.invoke` called the server directly, so none of
the call-time controls in your configuration applied to them. They now run the
call through the same executor as `hangar_call`: tool access and withdrawals,
digest pins, validators and interceptors, approval, the global and per-server
concurrency limits, and tenant budgets all apply.

**Pass the caller.** `invoke` takes a new, optional `principal=`. The call is
authorized for `tool:invoke` as an authenticated `hangar_call` caller is, by the
roles your configuration gives that principal id and its groups. The
principal's `tenant_id` is the tenant the per-tenant controls are applied for.

```python
from mcp_hangar import Hangar
from mcp_hangar.domain.value_objects import Principal, PrincipalId, PrincipalType

caller = Principal(
    id=PrincipalId("agent-1"),
    type=PrincipalType.SERVICE_ACCOUNT,
    tenant_id="team-a",
)

async with Hangar.from_config("config.yaml") as hangar:
    result = await hangar.invoke("math", "add", {"a": 1, "b": 2}, principal=caller)
```

`SyncHangar.invoke` takes the same `principal=`.

Nothing verifies the principal: your application vouches for its id, groups
and tenant, as an authenticator does for a request. `Principal.system()` is
refused with `ValueError`, because authorization grants the system principal
every permission.

**Without a principal, the call is an anonymous caller's**, the same as an
unauthenticated `hangar_call`:

- With authentication configured, it is refused with the code
  `AuthorizationDenied`.
- It carries no tenant. With `execution.tenant_limits` set, it shares the
  budget of callers with no tenant, built from the `"*"` entry, and is refused
  with `TenantQuotaExceeded` when there is no `"*"` entry.

There is no way to make an unchecked call. If your configuration refuses
anonymous callers, pass a principal.

**What a call that does not succeed raises.**

- A call a control refuses, or one that fails upstream, raises the new
  `ToolCallFailedError`. It is a `ToolInvocationError`, so an
  `except ToolInvocationError` still catches it. Its `code` is the `error_type`
  that `hangar_call` reports for the same call: for example
  `ToolAccessDeniedError`, `ToolWithdrawnError`, `ValidatorDenied`,
  `TenantQuotaExceeded` or `CircuitBreakerOpen`. Its message is the text
  `hangar_call` reports.
- An upstream failure that `invoke` let through as its own exception type,
  such as `ToolTimeoutError` or `ClientError`, is now a `ToolCallFailedError`
  whose `code` names that type.
- `McpServerNotFoundError`, `ToolNotFoundError` and `TimeoutError` are raised
  as before.
- A server that fails to start used to raise `McpServerStartError`. It now
  raises `ToolCallFailedError` with `code == "McpServerStartError"`, so an
  `except McpServerStartError` no longer catches it.
- The result is still returned whole. The per-call size limit (10 MB) and a
  `truncation:` section cut `hangar_call` results, not the results `invoke`
  returns, and no continuation is stored for an `invoke` call.

**A tool that needs approval.**

- `invoke` raises `TimeoutError` at `timeout_s`, and the event loop is not
  blocked while the call waits.
- The call still holds one of the facade's pool threads until the approval is
  decided or expires (`approval_timeout_seconds`, 300 seconds by default). An
  approval given after `invoke` timed out is refused, so the tool does not run.
- That pool also runs `stop()` and `health()`, and each pending approval takes
  one of its threads. Size it with `HangarConfig().max_concurrency(...)` for
  the approvals that can be pending at once.
- `SyncHangar.invoke` blocks the calling thread for up to `timeout_s`.

**Also:**

- `invoke` accepts a group id, as `hangar_call` does, and the call goes to the
  member the group selects.
- `timeout_s` still bounds the wait. The call itself is given `timeout_s`
  clamped to 1-300 seconds, as `hangar_call` clamps its `timeout`.
- A facade call now writes the `hangar_call` span and log lines, and is counted
  in the batch metrics.
- A call through `invoke` has no session and no request headers: session
  suspension does not apply to it, and an L7 rule that selects on
  `Mcp-Param-*` does not fire, as for `hangar_call` over stdio.
