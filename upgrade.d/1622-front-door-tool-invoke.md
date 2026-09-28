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

With auth off (the default, and `--unsafe-no-auth`) nothing changes. A stdio
front door with `auth.enabled: true` is refused as unauthenticated whatever
`auth.stdio.principal` declares, as `hangar_call` over stdio already was:
no request carries a principal on stdio. A stdio deployment that relied on
flat calls with auth on should run with auth off, which is how ADR-026
expects stdio to be configured.
