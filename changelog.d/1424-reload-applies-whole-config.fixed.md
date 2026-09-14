**core:** a configuration reload now applies the whole configuration and keeps
the topology mode. A reload reset `tool_access.mode` to `egress` and applied
only `mcp_servers`. On a `front_door` gateway, a caller with no tenant identity
was then resolved by egress rules. `interceptors`, `ui_resources`,
`headers.param_validation`, `resource_links` and `execution` kept their boot
values, even when deleted from the file. A reload also removed the tool-access
policies set at runtime, and between clearing the policies and registering them
again it resolved calls against none. This held for every trigger:
`POST /api/config/reload`, `hangar_reload_config`, SIGHUP and the file watcher.

A reload now applies every one of those sections through the function startup
uses, and a section deleted from the file goes back to its default. Every
section is checked before any server is stopped, so a bad value refuses the
reload and changes nothing. The policies, withdrawals, pins and
`header_exposure` blocks are swapped in as one set. A policy set at runtime is
kept, unless the file now defines the same scope. A file that changes
`tool_access.mode` is refused with HTTP 409 and changes nothing: the mode needs
a restart. See `UPGRADE.md`.
