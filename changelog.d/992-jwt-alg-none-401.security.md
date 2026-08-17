A Bearer JWT with `alg=none` (or any token whose signing key cannot be
resolved from the configured JWKS) is now rejected with a clean 401
`authentication_failed`, like every other invalid credential. Previously
`PyJWKClientError` escaped the JWT validator -- it is not an
`InvalidTokenError` subclass -- and surfaced as a raw 500, so a crafted
unsigned token produced an internal error where garbage Bearer produced 401.
