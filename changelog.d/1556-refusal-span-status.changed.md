**core:** a refusal no longer ends its span as an error. An egress policy denial,
a call routed to approval and a spent rate-limit budget are decisions the gateway
made on purpose, but every span they passed through ended ERROR, so an operator
counting error traces counted refusals, and the `hangar.gate.outcome=deny` from
the gate vocabulary sat on a span whose status said failure (ADR-029 s5).

`batch.call.<tool>` now stays UNSET when the call's outcome is `deny`, and so do
`dispatch.*`, `handler.*` and `command.send.*` when a refusal escapes them. A
failure still ends ERROR: a cold start that did not start, an approval gate that
could not be reached, an upstream that broke. Each refusal keeps the bounded
`error.type` naming what refused, and `dispatch.*` now reports
`hangar.dispatch.outcome=rejected` for every refusal rather than for a rate
limit alone -- an L7 denial raised by the aggregate read `error` there.
