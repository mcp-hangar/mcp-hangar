**security:** the front door's flat `tools/call` now checks the caller's
`tool:invoke` permission, with the same check `hangar_call` runs
(`_authorize_calls`). It never ran it, so with auth on a principal without
`tool:invoke`, such as one holding only `viewer`, could invoke any projected
upstream tool through the front door that `hangar_call` refused it. A refused
flat call is a tool error (`isError`) reading
`Not authorized to invoke tool '<tool>': tool:invoke permission required`, the
tool is not executed, and one `ToolCallRefused` is audited with
`gate=authorization`. An allowed call's audit record carries the role that
admitted it in `mcp.caller.roles`. Over stdio with auth on, both
`hangar_call` and the flat call now decide `tool:invoke` for the principal
`auth.stdio.principal` declares, on its declared roles: a declared principal
holding `tool:invoke` is served, where `hangar_call` refused every stdio caller
as unauthenticated. An HTTP request never takes the declared principal. Auth
off is unchanged (#1622).
