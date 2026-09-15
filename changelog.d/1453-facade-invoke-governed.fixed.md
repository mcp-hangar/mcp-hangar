**core:** the Python facade's `Hangar.invoke` and `SyncHangar.invoke` now run
each call through the executor behind `hangar_call`, so the configured
call-time controls apply to them: tool access and withdrawals, digest pins,
validators and interceptors, approval, the global and per-server concurrency
limits, and tenant budgets. They called the server directly before, and none of
these applied. `invoke` takes a new, optional `principal=`: the call is
authorized for `tool:invoke`, and its per-tenant controls are applied, for that
principal, as for an authenticated `hangar_call` caller. Without one, the call
is an anonymous caller's, as an unauthenticated `hangar_call` is, so an embedder
whose configuration refuses anonymous callers now has to pass a principal. A
refused or failed call raises the new `ToolCallFailedError`, a
`ToolInvocationError` whose `code` is the `error_type` that `hangar_call`
reports for the same call. `invoke` still returns the whole result: response
truncation does not apply to it, and no continuation is stored for it. It also
accepts a group id now. See `UPGRADE.md`.
