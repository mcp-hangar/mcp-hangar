### a refused call is no longer an error trace

Spans Hangar owns end UNSET when a call was refused on purpose: a gate `deny`, an
egress policy denial, a call routed to approval, a spent rate-limit budget. They
ended ERROR before.

**If you count refusals by looking for error traces, that query stops matching
them.** What to use instead, all of it already on the span:

<!-- config-check: skip -->

```text
# before -- everything that failed OR was refused
status = ERROR

# after
status = ERROR                      # failures only
hangar.call.outcome = "deny"        # a refused batch call
hangar.refusal.gate, hangar.refusal.reason   # which gate refused it, and why
hangar.dispatch.outcome = "rejected"         # a refusal at the command bus
```

The bounded `error.type` is unchanged and still names what refused (for example
`EgressPolicyDeniedError`), so a query keyed on it keeps working. The `exception`
event is no longer added for a refusal.

An alert on Hangar's error rate now excludes refusals. That is the point of the
change, but it does mean a dashboard that looked busy under a denying policy will
read lower without anything having changed upstream.

`hangar.dispatch.outcome` is `rejected` for every refusal that reaches the
command bus, not only a rate limit; an L7 denial reported `error` there before.
The SDK's own SERVER span is untouched and still ends ERROR for a refused call,
as ADR-029 s5 records.
