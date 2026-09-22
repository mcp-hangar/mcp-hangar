**core:** every refused batch call now logs one `batch_call_refused` warning
naming the gate that refused it and the bounded reason, the same pair the call's
span carries. A refusal was legible in a trace after #1285 and still not in the
log: `_log_call_failure` counted a call as refused for two egress error types
only, and it runs on the invoke path, which a gate refusal never reaches. Each
gate wrote its own line instead -- `tool_withdrawn_rejected`,
`tool_digest_pin_rejected`, `tool_access_denied`, `tool_digest_pin_unresolvable`
-- under its own name and at its own level, so there was no one query for "which
calls were refused". Those four lines are now debug, and both sinks read one
classification, so the log and the span cannot disagree (ADR-029 s5). A gate that
broke rather than refused still logs `batch_call_failed` at debug.
