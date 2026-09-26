**core:** `batch.call.<tool>` now records the L7 egress policy verdict the aggregate applied:
`hangar.l7.verdict` (`allow`, `audit_observed`, `deny`, `require_approval`, `approval_honored`),
`hangar.l7.mode`, `hangar.l7.rule_kind` and `hangar.l7.policy_id`. Reasons, argument values and
header values are never exported. An Audit-mode observation does not mark the call refused. A call
refused because its arguments could not be inspected now ends `batch.call.<tool>` ERROR with
`hangar.call.outcome=error`. The `batch_call_refused` line for an L7 refusal carries `l7_verdict`,
`l7_mode`, `l7_rule_kind` and `l7_inspection_failed` in place of the policy's reasons as free text.
