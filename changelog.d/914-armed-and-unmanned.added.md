An approval gate that is armed but notifies nobody now says so at startup, and
`approval_channel` finally selects a delivery.

**The signal.** When a policy gates a tool and the channel that would notify for
it reaches nothing outside the process — `noop`, or a vendor name no installed
package claims — the startup check logs `subsystem_configured_but_unreachable`
at ERROR, naming the scope and the channel. It does not refuse the boot: the
gate is already fail-closed by timeout, so what is missing is a signal, not an
enforcement, and refusing over a notification channel would turn a degraded
notify path into an outage. A deployment that wants the stricter reading sets
`approvals: {delivery: {required: true}}` and gets a refusal instead.

Why it matters even though nothing leaks: every gated call hangs for
`approval_timeout_seconds` and then errors, which from the outside looks like a
broken gateway. The remediation reached for under that pressure is emptying
`approval_list` — fail-closed in code, fail-open in the organisation.

**`approval_channel` routes.** It was documented as the delivery channel for a
policy's approvals, merged with care across scope narrowing, and dispatched
nowhere: one global delivery handled every approval whichever policy raised it,
so per-server channels were silently one channel. Approvals now go through the
channel their policy names, resolved on first use so a policy arriving from a
hot reload or over REST is routable too. An unset `approval_channel` — the
default — means the deployment's `approvals.channel`, as before.

**Metrics.** `mcp_hangar_approval_requests{channel}` against
`mcp_hangar_approval_deliveries{channel,outcome}` (`sent`, `failed`,
`not_notified`) and `mcp_hangar_approval_decisions{channel,decision}`
(`granted`, `denied`, `expired`). Requests climbing while deliveries stay at
zero is the armed-and-unmanned shape; `expired` climbing beside a flat `sent` is
the same story from the other end.
