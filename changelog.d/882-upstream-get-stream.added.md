**core:** the gateway now opens and holds the standing `GET` stream of a
remote (Streamable HTTP) upstream, so server-initiated messages finally have
somewhere to land. `notifications/tools/list_changed` triggers rediscovery --
a changed upstream catalogue no longer persists stale until the next restart.
The upstream's MCP-protocol log notifications are deliberately not routed
(SEP-2577 deprecates the Logging surface). An upstream that answers the
`GET` with 404/405 simply has no
channel; that is detected once and left alone. On shutdown, a legacy
session-based upstream's negotiated session is now terminated with a `DELETE`
instead of being abandoned to its server-side timer. Progress-token
translation (#883) rides this channel and ships separately
