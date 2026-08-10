**core:** `tool_access.mode: front_door` projected zero tools to every
authenticated tenant over Streamable HTTP. The SDK hands each lowlevel handler a
per-request context carrying the HTTP request, and therefore the authenticated
principal; the `tools/list` and `tools/call` adapters were handed it and dropped
it, so both read `identity_context_var` -- which the ASGI wrapper sets in a
different task -- found nothing, and the resolver took its `member_id is None`
deny-all branch. An empty tool list is indistinguishable from "no tools
configured", so nothing said so. Both adapters now bind the caller for the
duration of the call, through the same bridge tool bodies already used
