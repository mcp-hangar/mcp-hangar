### a stdio front door advertises tools.listChanged

A `front_door` gateway served over stdio used to answer `initialize` with
`tools.listChanged: false`, so a client listed once and kept that answer until
it reconnected. It now answers `tools.listChanged: true` on protocol 2025-11-25
and earlier, and sends `notifications/tools/list_changed` when what that client
is projected changes.

Nothing needs changing for a client that follows the spec: it re-lists on the
notification. A client that cached on the strength of the old `false` and
ignores the notification behaves as before. `front_door` over HTTP and `egress`
still advertise `false`, and a client pinned to one replica is notified by that
replica about its own catalogue (#877).
