### An L7 refusal logs bounded verdict fields, and an uninspectable call is an error trace

`batch_call_refused` for an L7 egress refusal (`error_type` `EgressPolicyDeniedError` or
`EgressPolicyApprovalRequiredError`) no longer carries `reason`, which held the policy's
reasons joined into one free-text string, such as `tool 'x' matched a deny rule`. It
carries `l7_verdict` (`deny` or `require_approval`), `l7_mode` (`enforce`), `l7_rule_kind`
(`tool`, `argument` or `header`) and `l7_inspection_failed`, next to the `policy_id` it
already had. A log query or alert matching text in `reason` for these lines has to move
to those fields. The reasons themselves are still in the `egress_policy_enforced` warning
and in the `EgressPolicyEnforced` event.

A call the policy refused because it could not inspect the arguments (they could not be
serialized, or the inspection raised) is now `hangar.call.outcome=error` and ends
`batch.call.<tool>` ERROR, where it used to read `deny` and UNSET: the verdict is a
denial, but the evaluator broke. Its log line is `batch_call_failed` at warning with
`l7_inspection_failed=true`, not `batch_call_refused`. The spans inside the call stay
UNSET. The caller is refused exactly as before. In Audit mode nothing changes on the span;
the `egress_policy_violation_observed` warning carries `inspection_failed`.
