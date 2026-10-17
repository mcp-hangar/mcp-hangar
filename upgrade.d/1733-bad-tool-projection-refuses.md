### A misspelled `digest_enforcement` or a malformed `tool_projection` now refuses the configuration

A server or group `tool_projection` block that is not a mapping, or whose `digest_enforcement` is not `audit`, `warn` or `block`, used to be ignored (the second with one log line) and the entry ran on the default mode. It is now a `ConfigurationError` naming the scope and the value: the gateway will not start with it, a reload leaves the running configuration untouched, and `mcp-hangar config check` reports it.

Run `mcp-hangar config check` before upgrading. If it names an entry, correct the value; the entry was running on the default `block` mode all along.
