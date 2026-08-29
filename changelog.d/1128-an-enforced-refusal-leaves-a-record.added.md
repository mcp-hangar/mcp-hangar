**core:** an Enforce-mode egress refusal now leaves a record. Audit mode -- the
mode that by definition changes nothing -- emitted a domain event, a warning and
a metric; Enforce mode, refusing the call for real, emitted a `logger.debug`
line in the batch fault barrier carrying the generic caller-facing message, with
the reasons the policy computed dropped on the way out. An operator could not
answer "which calls did this policy refuse yesterday" from anything Hangar
wrote, and `mcp_hangar_tool_call_errors_total` cannot cover it: it is fed from
`ToolInvocationFailed`, whose three emitters are all past the gate. A refusal
now publishes `EgressPolicyEnforced` -- the same fields as the observed event
plus the applied action, so one exporter reads both -- increments
`mcp_hangar_egress_policy_enforced_total{action,rule_kind}`, and logs at warning
with its reasons and the policy that produced them. A `requireApproval` verdict
nobody answered is recorded as the refusal it is. The fault barrier now
distinguishes a deliberate refusal from an upstream failure: the refusal is a
warning naming the reason, everything else keeps the debug level so a batch of
failing calls is not a log flood
