### GET /mcp: a list_changed stream on a front door, 405 in egress

On the handshake era (2025-11-25 and earlier), `GET /mcp` used to open an
empty SSE stream that nothing wrote to. It stayed open until the client left.
It now does one of two things:

- On a `front_door` gateway, it is the stream that carries
  `notifications/tools/list_changed`, and `initialize` answers
  `tools.listChanged: true`. A client that re-lists on the notification now
  sees upstreams that arrive after it connected, without reconnecting.
- In `egress`, it answers `405 Method Not Allowed`. MCP clients treat a 405 on
  this GET as "no stream offered".

A proxy in front of the gateway should pass `text/event-stream` responses
unbuffered and allow an idle interval longer than 15 s, the ping interval.
On each replica, one principal may hold 32 open streams and one tenant 256. A
client past either gets 429 on the GET, while its POSTs are unaffected. A
stream ends after an hour, and the client reconnects and authenticates again. A stream is told about the
catalogue of the replica it is connected to (#877).
