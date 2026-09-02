**core:** `POST /config/export` and `GET /config/diff` returned a subprocess
server's `env` verbatim and the `auth`, `discovery` and other pass-through
sections with `${VAR}` references already resolved, so a principal holding
`config:read` could read the gateway's credentials out of the exported YAML.
`to_config_dict` redacted the remote `auth` block only -- and its docstring
claimed more than it did -- while `_sanitize` covers `GET /config` alone and
only matches top-level key names. Both surfaces are redacted now, contents
first so a section named `auth` keeps its harmless settings
