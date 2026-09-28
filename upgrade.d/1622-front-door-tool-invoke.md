### A front-door caller without `tool:invoke` can no longer call upstream tools

With auth on, the front door's flat `tools/call` now requires the `tool:invoke`
permission, as `hangar_call` has since #389. A principal whose roles lack it,
such as one holding only `viewer`, gets a tool error
(`Not authorized to invoke tool '<tool>': tool:invoke permission required`)
where it used to be served. An unauthenticated or anonymous caller is refused
as `Authentication required to invoke tools`.

Grant a role that holds `tool:invoke` (`developer`, `service-account`,
`admin`) to every principal that calls tools through a front door, for
example in `auth.role_assignments`:

```yaml
auth:
  role_assignments:
    - principal: "group:agents"
      role: service-account
      scope: global
```

Listing is unchanged: such a caller still sees the tools its tool-access
policy allows, and is refused when it calls one.

With auth off (the default, and `--unsafe-no-auth`) nothing changes.

Over stdio with `auth.enabled: true`, both invoke paths now decide
`tool:invoke` for the principal `auth.stdio.principal` declares, on the roles
it declares. The default declaration is `roles: [viewer]`, which does not hold
`tool:invoke`, so its flat calls are now refused where they used to be served.
Declare `developer` or `service-account` where a stdio session must call tools:

```yaml
auth:
  stdio:
    principal:
      id: local-user
      tenant_id: local
      roles: [developer]
```

The same declaration also makes `hangar_call` over stdio with auth on serve a
caller it used to refuse as unauthenticated. The declared roles are the
principal's only role source: the configured role store and any OPA policy are
not consulted for it.
