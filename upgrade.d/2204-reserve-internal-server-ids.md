### a configuration that declares a server named `_global` is refused

`_global` is now a reserved server id. A configuration file whose
`mcp_servers` section has a server, group or group member with the id
`_global` used to start with that entry skipped and a warning
(`skipping_invalid_mcp_server_id`, or `skipping_invalid_member_id` for a group
member); startup and a reload now refuse the file with a `ConfigurationError`
that names the id. Remove the entry or give it another id. `hangar_load`
likewise refuses a registry entry whose sanitized id is `_global`. A
gateway-wide policy set for `_global` through the tool access policy endpoint
(scope `provider`) is not affected.
