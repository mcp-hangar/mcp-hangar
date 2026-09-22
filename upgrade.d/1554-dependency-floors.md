### OIDC signing keys are fetched over https only, and old pyjwt / cryptography / python-multipart are upgraded

#### Dependency floors

Upgrading raises `pyjwt` to at least 2.13.0 (now with its `[crypto]` extra),
`cryptography` to at least 50.0.0, `python-multipart` to at least 0.0.31 and
`pydantic` to at least 2.12.0. An environment that already had older versions
installed has them upgraded by the same `pip install --upgrade mcp-hangar`. If
you pin any of these yourself, for example in a constraints file or a lockfile
of your own, raise the pin, or the install fails to resolve.

#### A non-https `jwks_uri` or discovery issuer no longer starts

Before, an `auth.oidc` issuer or `jwks_uri` that was not `https://` logged
`oidc_issuer_not_https` or `jwks_uri_not_https` and was used anyway. Whoever
can answer a plain-HTTP request to that URL chooses the keys every token is
verified against, so the gateway now refuses to start with a `ConfigurationError`
that names the field and the value.

What is checked is the URL the keys come from: `jwks_uri` when it is set,
otherwise the `issuer`, whose `/.well-known/openid-configuration` names the
JWKS. When `jwks_uri` is set, the issuer is not fetched, only matched against the
token's `iss`, and is not checked. A `jwks_uri` returned by discovery that is
not https is refused as well. That check happens at the first token, not at
startup, so it shows as `401` responses with `jwks_uri_not_https` in the log.

`http://localhost`, `http://127.0.0.1` and `http://[::1]` are still accepted,
for an IdP on the same host during development. Every other `http://` host is
refused, including a service name inside a compose network or a cluster, as are
all other schemes (`file://` included).

To move across, serve the IdP over TLS and use its https URL:

```yaml
auth:
  oidc:
    enabled: true
    issuer: https://keycloak.example.com/realms/mcp-hangar
    audience: mcp-hangar
```
