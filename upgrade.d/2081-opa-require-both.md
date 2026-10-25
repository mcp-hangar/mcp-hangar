### With OPA enabled, decide whether it may widen RBAC

OPA has always been asked only when RBAC denies, and an OPA allow overrides that
deny, with no tenant scope: a principal with no role, or one confined to a
tenant, is admitted to whatever the policy allows. That is unchanged, and the
boot log now says so (`opa_may_widen_rbac`).

To have OPA only refuse more -- both must allow -- set:

```yaml
auth:
  opa:
    enabled: true
    require_both: true
```

With `require_both: true`, a principal RBAC refuses stays refused whatever the
policy says, so a policy written to admit callers RBAC does not know about stops
admitting them.
