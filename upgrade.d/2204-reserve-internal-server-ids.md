### a configuration that declares a server named `_global` is refused

`_global` is now a reserved server id. A configuration file whose
`mcp_servers` section has a server, group or group member with the id
`_global` used to start with that entry skipped and a
`skipping_invalid_mcp_server_id` warning; startup and a reload now refuse the
file with a `ConfigurationError` that names the id. Remove the entry or give it
another id. `hangar_load` likewise refuses a registry entry whose sanitized id
is `_global`. A policy set for every server through `mcp_server_id: "*"` is
not affected.
