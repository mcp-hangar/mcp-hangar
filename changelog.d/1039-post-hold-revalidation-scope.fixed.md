**core:** the post-approval-hold re-check asks the resolver the same question the
pre-hold gate asked. It re-resolved the effective policy with neither the target
group nor the caller's tenant, although both were in scope: in `front_door` that
is the fail-closed missing-identity branch, so **every** human-approved call was
refused at dispatch with `Approval no longer valid at dispatch: tool is no longer
allowed by policy`, and in `egress` a deny added to a group's policy during the
hold -- the race this re-check exists to close -- was not seen
