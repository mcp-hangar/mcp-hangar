### Bind each trusted OIDC issuer to its tenants with `allowed_tenants`

With two or more entries in `auth.oidc.issuers`, each issuer could sign a token
naming any tenant, and its `sub` and `groups` become the same principal and
groups another issuer's do. Boot now logs one `oidc_issuers_not_bound_to_tenants`
warning naming every issuer without a binding. Nothing is refused until you add
one.

```yaml
auth:
  oidc:
    enabled: true
    issuers:
      - issuer: https://issuer-a.example.com
        audience: https://gateway.example.com
        allowed_tenants: [team-a]
      - issuer: https://issuer-b.example.com
        audience: https://gateway.example.com
        allowed_tenants: [team-b]
```

- A bound issuer's token is refused with `401` unless its tenant claim is a
  string in the list. A token naming no tenant is refused as well.
- The list must name at least one tenant. An empty list, a bare string, or the
  key with no value (YAML null) refuses the configuration rather than leaving
  the issuer unbound.
- The key is per issuer. Set at the top level of `auth.oidc` it refuses the
  configuration at boot, as does a bare string, an empty list or a non-string
  entry.
- The binding confines tenant-scoped roles only. A role assigned at scope
  `global` to a subject or to `group:<name>` still reaches a token with that
  subject or group from any trusted issuer. With more than one identity
  provider, grant OIDC users and groups only `tenant:<id>` roles, and do not
  list one tenant under two issuers.
- A single trusted issuer needs no change and logs no warning.
