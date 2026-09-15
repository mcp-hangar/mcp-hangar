**core:** each caller can have its own command-bus rate limit, and the
read-only tools are out of it. `rate_limit` was one budget per command type,
shared by every caller, so one caller's tool calls could use up
`InvokeToolCommand` for all of them. The same limit refused `hangar_group_list`
and the other listing tools.

`rate_limit` keeps its meaning, the budget all callers share, so no deployment
admits more than it did. The new `rate_limit.per_caller` (`rps`, `burst`) gives
each caller, by tenant and principal, a budget of its own under it. A caller
over its own budget is refused without spending the shared one. The listing and
inspection tools (`hangar_list`, `hangar_status`, `hangar_details`,
`hangar_group_list` and the others that change nothing) are no longer rate
limited. On every path, a refusal names the code, the budget, the limit and when
to retry, and the HTTP API adds `Retry-After`. See `UPGRADE.md`.
