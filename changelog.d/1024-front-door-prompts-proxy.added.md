**core:** an upstream's prompts are served through the front door. In
`front_door` mode `prompts/list` aggregates prompts per tenant across the
tenant's own projected upstreams (flat naming per the tool convention: bare
name, cross-server collisions drop both entries) and `prompts/get` relays to
the owning upstream, so the `prompts` capability is advertised exactly when
the proxy is active (#888 honesty rule preserved). MVP boundaries: no
prompt-level policy yet (anything from the tenant's own upstreams is allowed,
never another tenant's -- the governance seam is #1028) and no
`completion/complete` (#1026)
