**core:** a `front_door` gateway served over stdio now advertises
`tools.listChanged: true` on the handshake era (2025-11-25 and earlier) and
sends `notifications/tools/list_changed` when a tenant's projection changes:
the boot warm-up landing an upstream, a hot-loaded server, an upstream's own
`list_changed` (now also routed from stdio upstreams, which dropped it), a tool
found by a call's lazy refresh, and a withdrawal. A session is told only when
its own tenant's projection changed, and changes are coalesced over a 300 ms
window, flushed when the warm-up ends. `front_door` over HTTP and `egress`
still advertise `false`: HTTP has no back-channel until the sessionless GET
stream lands. The other three handshake-era flags and every 2026-07-28 flag are
unchanged, and the bounded first-listing wait (#1231) stays as a backstop.
