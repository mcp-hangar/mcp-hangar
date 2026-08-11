**core:** the README described digest pinning as failing closed without saying
what it needs to fire. A pin was addressable only per tenant, so on a gateway
with authentication off it matched nothing -- and the same list two lines down
states the front-door precondition plainly ("fail-closed on unknown identity"),
so the omission read as an absence of one rather than an oversight. The bullet
now names both forms: the all-tenants block that holds any caller, and the
per-tenant one that needs authentication for a caller to arrive carrying a
tenant
