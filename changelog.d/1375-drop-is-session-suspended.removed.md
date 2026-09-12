**core:** `mcp_hangar.server.api.sessions.is_session_suspended()` is removed.
Nothing in Hangar called it, and a helper that looked like the suspension
check while no request path used it is how session suspension went
unenforced (GHSA-fhwh-fmq2-7m5c). Calls are refused through
`mcp_hangar.server.session_guard`, and the registry itself is
`get_session_suspension_registry()`.
