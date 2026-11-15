### an HTTP upstream on 2026-07-28 is spoken to statelessly

Starting a `remote` server now sends `server/discover` before anything else. An upstream that answers with 2026-07-28 in `supportedVersions` gets no `initialize` and no `notifications/initialized`, and holds no `Mcp-Session-Id`. Its upstream log shows one `server/discover` per start in their place. Any other answer, including a method-not-found or an HTTP 400, falls back to the `initialize` handshake exactly as before, so an upstream on 2025-11-25 or earlier sees one extra refused `server/discover` and nothing else.

Hangar opens no standing `GET` stream to a stateless upstream, because the 2026-07-28 revision has none. Until Hangar listens with `subscriptions/listen`, it receives no unprompted message from such an upstream. That covers `notifications/tools/list_changed`, `notifications/resources/updated`, the other `*/list_changed` notifications it relays to front-door subscribers, and progress sent outside a response. A changed catalogue is picked up when the server restarts.

The configuration keys are unchanged, and no setting forces the `initialize` handshake. A deployment that relies on those notifications from an SDK 2.x upstream, which serves both eras, should stay on the previous release until `subscriptions/listen` ships.
