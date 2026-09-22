**core:** a tool call's trace now says what each batch gate decided, and which
one refused it. Every stage of the executor's gate chain records one
`hangar.gate.decision` event on `batch.call.<tool>` (ADR-029), with
`hangar.gate.name`, `hangar.gate.outcome` (`allow`, `deny`, `skip`, `deferred`
or `error`), a bounded `hangar.gate.reason` code when the gate can say why, and
the pinned digest as `hangar.gate.revision` on the pin gates. Before this only
three of the thirteen gates had a span, so a refused call's trace could not
name the gate that refused it.

A gate that does not apply now says `skip` rather than looking like a pass, and
a digest pin with no catalogue yet records `deferred` and then, on the same
span, what the check after the cold start decided. The span also carries
`hangar.call.outcome` and, for a refused call, `hangar.refusal.gate` and
`hangar.refusal.reason`.

Additive only: gate order, enforcement, and existing span names, attributes and
status are unchanged. Recording never runs a policy again and cannot change a
verdict; a failure to record is swallowed.
