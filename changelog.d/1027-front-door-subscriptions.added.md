**core:** `front_door` serves `subscriptions/listen`, so a client can be told
when an upstream resource changes. A stream's honored `resourceSubscriptions`
are filtered to the projected `hangar://` URIs its tenant can read, the owning
upstream is subscribed once however many streams ask, and an upstream
`notifications/resources/updated` is delivered to exactly those streams under
the projected URI. The three `*/list_changed` nudges ride the same stream.

This also makes an existing advertisement true rather than aspirational: on the
2026-07-28 wire the SDK derives `resources.subscribe` **and** all three
`listChanged` flags from whether `subscriptions/listen` is served, and
`MCPServer` registers a handler for it unconditionally -- so every deployment
advertised subscriptions that nothing published. Outside `front_door` mode that
handler is now withdrawn and the flags read `false`.

Note for clients: the legacy `resources/subscribe` is not the surface here. At
2026-07-28 a server has no standing channel to push an update on, so a
subscription taken that way could never fire; `subscriptions/listen` is the
wire that carries it. An upstream still has to offer the server->client stream
for updates to arrive at all.
