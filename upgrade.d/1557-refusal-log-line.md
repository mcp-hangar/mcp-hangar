### a refused call logs `batch_call_refused`, and the per-gate lines are debug

A batch call refused by a gate now logs exactly one warning:

```text
batch_call_refused  gate=withdrawal reason=tool_withdrawn tool=... mcp_server=...
                    tenant_id=... call_id=... error_type=ToolWithdrawnError elapsed_ms=...
```

`gate` and `reason` are the values the call's span carries as
`hangar.refusal.gate` and `hangar.refusal.reason`.

**If you alert on the per-gate lines, move those alerts to this one.** These four
were emitted at info and are now debug, which a default deployment does not
print, because each only restated a refusal that is now reported once:

- `tool_withdrawn_rejected`
- `tool_access_denied`
- `tool_digest_pin_rejected`
- `tool_digest_pin_unresolvable`

`tenant_quota_exceeded` is unchanged: it carries the budget and the approval it
spent, which the refusal line does not.

Warning volume rises on a deployment that refuses calls routinely -- a strict
tool-access policy, a withdrawn tool still being called. That is the intended
trade (ADR-029 s5): one refusal is one warning, in the log and in the trace.
