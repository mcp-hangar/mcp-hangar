**core:** fixes on the auth and config-error surfaces. `auth bootstrap-admin`
no longer prints an API key that no authenticator would accept (an OIDC-trusted
deployment with `auth.api_key.enabled: false`), and a flagless re-run no longer
claims the one-shot claim is unspent when it has already been spent -- both
answers now consult the store first, via a new read-only
`is_initial_admin_bootstrapped` check that costs nothing. `POST /api/config/reload`
maps only a genuine "cannot write the backup file" condition to `503`; an
operator-input config error is now a `500` with a sanitised message instead of a
retryable `503` that surfaced internal exception text (paths, server ids) to the
caller. The auth store's read-only PostgreSQL paths now commit or roll back
rather than leaving a borrowed connection idle in transaction.
