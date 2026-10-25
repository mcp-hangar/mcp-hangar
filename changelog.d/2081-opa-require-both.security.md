**security:** `auth.opa.require_both: true` makes OPA able only to refuse more:
RBAC and OPA must both allow. With OPA enabled the default is unchanged -- OPA is
asked only when RBAC denies, and its allow overrides that deny and its tenant
scope -- but it is no longer silent: the gateway logs `opa_may_widen_rbac` at boot.
A `require_both` that is not a boolean refuses the configuration
