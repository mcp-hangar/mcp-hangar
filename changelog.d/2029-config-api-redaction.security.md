**security:** `GET /api/config/` and `GET /api/config/diff` no longer serve secrets.
Both ran a sanitizer over top-level key names only, so `GET /config` returned
every API-registered server's `env` verbatim, and the diff -- computed from the
file after `${VAR}` interpolation -- carried the real value of an environment
secret in `on_disk` and in the unified `diff` string. Both now redact the way
`POST /api/config/export` does: sensitive key names at every level, and
credential-shaped values. Both sides of the diff are redacted before it is
computed, so an unchanged file still reports no diff
