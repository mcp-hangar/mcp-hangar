### A group call's audit record names the group in `mcp.server.id`; the member is `hangar.route.backend`

The `tool_invocation` audit record of a call routed through a server group used
to carry the selected member in `mcp.server.id`. It now carries the group the
caller named, the same value its spans carry since #1286, and the member moves to
`hangar.route.backend`.

An audit query, dashboard or alert that selects group calls by member finds
nothing after the upgrade. Change it:

- Old: `mcp.event.name = "tool_invocation" AND mcp.server.id = "<member>"`
- New: `mcp.event.name = "tool_invocation" AND hangar.route.backend = "<member>"`

A query by group, `mcp.server.id = "<group>"`, used to need the group's
membership at the time of each call and now matches directly. Calls to a server
outside any group carry the same value in both keys, so their queries need no
change.

The compliance exporters follow the same rule. For a group call, the server
field now names the group: CEF `cs1` (`cs1Label=ProviderID`), LEEF `src`, syslog
`provider` and JSON lines `provider_id`. The member moves to a new field: CEF
`flexString1` (`flexString1Label=RouteBackend`), LEEF `routeBackend`, syslog
`routeBackend` and JSON lines `route_backend`. A SIEM rule that matched the
member in the server field, such as CEF `cs1=<member>`, should match
`flexString1=<member>` instead.

Records written before the upgrade are not rewritten. Replayed events from
before it carry no group, so their records fall back to the member in both keys.
