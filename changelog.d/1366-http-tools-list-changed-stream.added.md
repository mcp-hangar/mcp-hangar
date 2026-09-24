**core:** a `front_door` gateway served over HTTP now advertises
`tools.listChanged: true` on the handshake era (2025-11-25 and earlier). It
sends `notifications/tools/list_changed` on a sessionless `GET /mcp` stream,
which the TypeScript SDK client opens after `notifications/initialized`. The
stream sends one notification as it opens. After that, it sends one only when
that caller's tenant projection changes. It passes the same authentication,
DNS-rebinding guard and session-suspension check as a POST. A suspension also
ends a stream that is already open. Each principal may hold 32 streams and each
tenant 256 (429 past either). The stream pings every 15 s and ends after an
hour, so the client's reconnect authenticates again. POSTs stay stateless (#877).
Python SDK 2.0.0 clients open the stream only when they hold a session id, so
they still rely on the bounded first-listing wait (#1231). Where no push is
served (`egress`), `GET /mcp` on the handshake era now answers 405 instead of
holding an empty stream open.
