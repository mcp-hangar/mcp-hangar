**core:** `PolicyEvaluationResult.policy_id` and the `policy_id` argument of its
`allow()` / `deny()` constructors are gone. The field was documented as "the
policy that made the decision (for audit)" and was never set by anything and
never read by anything -- the only enforcer implementation in the codebase is
the null one, which allows everything and names no policy. A documented-but-
always-empty audit field is worse than an absent one, because a reader
reasonably assumes it works. Policy identity on the path that does produce
verdicts is `L7Policy.policy_id`
